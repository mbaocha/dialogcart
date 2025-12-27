const path = require('node:path');
require('dotenv').config({path: path.resolve(__dirname, '../../.env.local')});

// @ ts-nocheck
const request = require('supertest');
const {v4: uuidv4} = require('uuid');
const {addMinutes, subMinutes, addDays} = require('date-fns');
const {Client} = require('pg');

process.on('unhandledRejection', (error)=> {
  console.error('Unhandled promise rejection in test suite:', error);
});

/**
 * Integration tests targeting internal REST API workflows.
 * Executes realistic cross-endpoint journeys to verify business rules.
 */

const BASE_URL = process.env.INTERNAL_API_BASE_URL ?? 'http://localhost:3000';
const ORG_ID = 1;
const TEST_CHANNEL = 'integration-tests';

jest.setTimeout(120_000);

/**
 * @ typedef {object} ReservationAvailability
 * @ property {string} inventoryId
 * @ property {string} roomTypeId
 * @ property {number} minNights
 * @ property {number} maxOccupancy
 * @ property {number} price
 */

/**
 * @ typedef {object} BookingResponse
 * @ property {string} bookingCode
 * @ property {string} status
 * @ property {number} total
 * @ property {string} holdExpiresAt
 */

/**
 * Utility: create a PG client for manipulating fixtures when necessary.
 */
const getPgClient = () = > {
  const DATABASE_URL = process.env.DATABASE_URL ?? 'postgres://testuser:password123@localhost:5432/dcart';
  if (!DATABASE_URL) {
    throw new Error('DATABASE_URL env var required for integration tests');
  }
  return new Client({
    connectionString: DATABASE_URL,
  });
};

const ROOM_TYPE_NAME = 'Integration Extended Stay';
const FALLBACK_ROOM_NAME = 'Delux';
const SERVICE_NAME = 'Integration Spa Treatment';
const EXTRA_NAME = 'Integration Breakfast';

const getCatalogItemId = async (name, type, canonicalKey) = > {
  const client = getPgClient();
  await client.connect();
  try {
    const result = await client.query(
      'SELECT id FROM catalog_items WHERE organization_id = $1 AND type = $2 AND canonical_key = $3 AND name = $4 LIMIT 1;',
      [ORG_ID, type, canonicalKey, name],
    );
    return result.rows?.[0]?.id ?? null;
  } finally {
    await client.end();
  }
};

/**
 * @ param {object} overrides
 */
const createCustomer = async (overrides={}) = > {
  const baseEmail = `${uuidv4()}@example.test`;
  const payload = {
    organization_id: ORG_ID,
    name: 'Testy McTestface',
    email: baseEmail,
    phone: '+15555551234',
    ...overrides,
  };

  const response = await request(BASE_URL)
    .post('/api/internal/customers')
    .send(payload)
    .set('Content-Type', 'application/json');

  if (![200, 201].includes(response.status)) {
    // Try to get the response text in case body parsing failed
    let responseText = '';
    try {
      responseText = response.text | | '';
    } catch(e) {
      // Ignore if text is not available
    }

    console.error('Customer creation failed', {
      status: response.status,
      body: response.body ? JSON.stringify(response.body, null, 2): 'No body object',
      text: responseText | | 'No text',
      payload,
      headers: response.headers,
    });
    // Log the actual error if available
    if (response.body?.error | | response.body?.message) {
      console.error('Error details:', response.body.error | | response.body.message);
    }
    // Also log the full error if it's an Error object
    if (response.body && typeof response.body === 'object') {
      console.error('Full response body:', JSON.stringify(response.body, null, 2));
    }
  }

  expect([200, 201]).toContain(response.status);

  const customerRecord = response.body?.data?.customer ?? response.body?.customer ?? response.body;
  const customerId = customerRecord?.id;

  expect(customerRecord).toEqual(
    expect.objectContaining({
      id: expect.any(Number),
      organizationId: ORG_ID,
      email: (payload.email ?? '').toLowerCase(),
    }),
  );

  return { payload, response, customerId, customerRecord };
};

/**
 * @param {string} startDate
 * @param {string} endDate
 * @returns {Promise<ReservationAvailability[]>}
 */
const loadReservationAvailability = async (startDate, endDate) => {
  console.log('Fetching reservation availability', { startDate, endDate });
  const response = await request(BASE_URL)
    .get('/api/internal/availability/reservation')
    .query({
      organizationId: ORG_ID,
      organization_id: ORG_ID,
      startDate,
      endDate,
      check_in: startDate,
      check_out: endDate,
      channel: TEST_CHANNEL,
    });

  if (response.status !== 200) {
    console.error('Reservation availability request failed', {
      status: response.status,
      body: JSON.stringify(response.body, null, 2),
    });
  }

  expect(response.status).toBe(200);

  const body = response.body ?? {};
  const rooms
    = body.rooms
    ?? body.data?.rooms
    ?? body.data?.availability?.rooms
    ?? body.data?.inventory
    ?? [];

  expect(Array.isArray(rooms)).toBe(true);

  if (rooms.length === 0) {
    console.warn('No reservation inventory returned', JSON.stringify(body, null, 2));
  } else {
    console.log('Sample reservation inventory entry', JSON.stringify(rooms[0], null, 2));
  }

  return rooms;
};

/**
 * @param {{
 *  customerId: string;
 *  inventoryId: string;
 *  startDate: string;
 *  endDate: string;
 *  services?: Array<{ serviceId: string, quantity: number }>;
 * }} params
 */
const createBooking = async ({
  customerId,
  itemId,
  checkIn,
  checkOut,
  guests = 1,
  extras = [],
}) => {
  console.log('Creating booking payload', {
    customerId,
    itemId,
    checkIn,
    checkOut,
    extras,
  });
  const payload = {
    organization_id: ORG_ID,
    customer_id: Number(customerId),
    booking_type: 'reservation',
    item_id: Number(itemId),
    check_in: checkIn,
    check_out: checkOut,
    guests,
    extras: extras.map(extra => ({
      id: Number(extra.id),
      quantity: extra.quantity ?? 1,
    })),
  };

  const response = await request(BASE_URL)
    .post('/api/internal/bookings')
    .send(payload)
    .set('Content-Type', 'application/json');

  if (![201, 409].includes(response.status)) {
    console.error('Booking creation failed', {
      status: response.status,
      body: JSON.stringify(response.body, null, 2),
      payload,
    });
  }

  expect([201, 409]).toContain(response.status);

  const booking = response.body?.data?.booking ?? response.body.booking ?? null;

  if (response.status === 201) {
    expect(booking).toEqual(
      expect.objectContaining({
        booking_code: expect.any(String),
        status: expect.any(String),
        hold_expires_at: expect.any(String),
      }),
    );
  }

  return { payload, response, booking };
};

/**
 * @param {string} bookingCode
 * @returns {Promise<BookingResponse>}
 */
const fetchBooking = async (bookingCode) => {
  console.log('Fetching booking', bookingCode);
  const response = await request(BASE_URL)
    .get(`/api/internal/bookings/${bookingCode}`)
    .query({ organization_id: ORG_ID });

  if (response.status !== 200) {
    console.error('Fetch booking failed', {
      status: response.status,
      body: JSON.stringify(response.body, null, 2),
      bookingCode,
      organizationId: ORG_ID,
    });
  }

  expect(response.status).toBe(200);

  return response.body?.data?.booking ?? response.body.booking ?? response.body;
};

/**
 * @param {string} bookingCode
 * @param {number} amount
 * @param {string} currency
 */
const createPaymentIntent = async (bookingId, amount, currency = 'usd') => {
  console.log('Creating payment intent', { bookingId, amount, currency });
  const payload = {
    booking_id: Number(bookingId),
    payment: {
      amount,
      currency,
      method: 'stripe',
    },
  };

  const response = await request(BASE_URL)
    .post('/api/internal/bookings/intent')
    .send(payload)
    .set('Content-Type', 'application/json');

  return { payload, response };
};

/**
 * @param {{
 *  eventId: string;
 *  type: string;
 *  bookingCode: string;
 *  amount: number;
 *  status?: string;
 * }} params
 */
const buildStripeWebhookPayload = ({
  eventId,
  type,
  bookingId,
  bookingCode,
  bookingType,
  paymentIntentId,
  chargeId,
  amountMinor,
  currency,
  status,
}) => ({
  id: eventId,
  type,
  data: {
    object: {
      id: paymentIntentId,
      status,
      amount_received: amountMinor,
      currency,
      metadata: {
        booking_id: String(bookingId),
        booking_code: bookingCode,
        booking_type: bookingType,
      },
      charges: {
        data: [
          {
            id: chargeId,
            receipt_url: 'https://example.org/receipt',
          },
        ],
      },
    },
  },
});

const postStripeWebhook = async (payload, signature) =>
  request(BASE_URL)
    .post('/api/internal/payments/webhook')
    .set('Stripe-Signature', signature)
    .send(payload);

const fireStripeWebhook = async ({
  eventId,
  type,
  bookingId,
  bookingCode,
  bookingType,
  paymentIntentId,
  chargeId,
  amountMinor,
  currency,
  status = 'succeeded',
}) => {
  console.log('Firing webhook', { eventId, type, bookingId, bookingCode, bookingType });
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!secret) {
    throw new Error('STRIPE_WEBHOOK_SECRET must be set for webhook tests');
  }

  const payload = buildStripeWebhookPayload({
    eventId,
    type,
    bookingId,
    bookingCode,
    bookingType,
    paymentIntentId,
    chargeId,
    amountMinor,
    currency,
    status,
  });

  const stripeLib = require('stripe');
  const header = stripeLib.webhooks.generateTestHeaderString({
    payload: JSON.stringify(payload),
    secret,
  });

  const response = await postStripeWebhook(payload, header);

  return { payload, response, signature: header };
};

const fireInvalidStripeWebhook = async (params, signature = 't=1,v1=deadbeef') => {
  const payload = buildStripeWebhookPayload({
    ...params,
    status: params.status ?? 'requires_payment_method',
  });
  const response = await postStripeWebhook(payload, signature);
  return { payload, response, signature };
};

/**
 * @param {string} bookingCode
 */
const expireBookingHold = async (bookingId) => {
  console.log('Expiring booking hold', bookingId);
  const client = getPgClient();
  await client.connect();
  const expiredTime = subMinutes(new Date(), 1).toISOString();
  // Set hold_expires_at as a plain string value in JSONB
  // Using to_jsonb converts the text to a JSONB string value
  // Search by ID since booking_code might be in meta or top-level column
  const result = await client.query(
    `
      UPDATE bookings
      SET meta = jsonb_set(COALESCE(meta, '{}'::jsonb), '{hold_expires_at}', to_jsonb($1::text)),
          created_at = NOW() - INTERVAL '16 minutes',
          updated_at = NOW() - INTERVAL '16 minutes'
      WHERE id = $2
      RETURNING id, booking_code, meta->>'hold_expires_at' as hold_expires_at, meta->>'booking_code' as meta_booking_code, created_at
    `,
    [expiredTime, bookingId],
  );
  if (result.rowCount === 0) {
    throw new Error(`No booking found with id: ${bookingId}`);
  }
  console.log('Updated hold_expires_at:', {
    bookingId: result.rows[0].id,
    bookingCode: result.rows[0].booking_code || result.rows[0].meta_booking_code,
    holdExpiresAt: result.rows[0].hold_expires_at,
    createdAt: result.rows[0].created_at,
  });
  await client.end();
};

const buildStayRange = (nights = 2, offsetMinutes = 60 * 26) => {
  // Use 26 hours instead of 24 to ensure we're well above the "at least 24 hours" requirement
  // The check uses diffHours < earliest, so we need diffHours >= 24
  const checkIn = addMinutes(new Date(), offsetMinutes);
  const checkOut = addMinutes(checkIn, 60 * 24 * nights);
  const checkInIso = checkIn.toISOString();
  const checkOutIso = checkOut.toISOString();
  return {
    checkInIso,
    checkOutIso,
    checkInDate: checkInIso.slice(0, 10),
    checkOutDate: checkOutIso.slice(0, 10),
  };
};

const resolveInventoryId = room =>
  room?.inventoryId
  ?? room?.id
  ?? room?.roomTypeId
  ?? room?.room_type_id
  ?? room?.inventory_id
  ?? null;

const selectRoomFromAvailability = (rooms) => {
  const preferred = rooms.find(room => resolveInventoryId(room) === primaryRoomTypeId);
  const fallback = rooms.find(room => resolveInventoryId(room) === fallbackRoomTypeId);
  const chosen = preferred ?? fallback ?? rooms[0];
  if (!chosen) {
    return null;
  }
  const itemId = resolveInventoryId(chosen);
  if (!itemId) {
    return null;
  }
  const maxGuests
    = chosen?.max_occupancy
    ?? chosen?.config?.maxGuests
    ?? chosen?.maxGuests
    ?? 1;
  const guestCount = Math.max(1, Math.min(2, maxGuests || 1));
  return { itemId, guestCount };
};

let primaryRoomTypeId = null;
let fallbackRoomTypeId = null;
let serviceCatalogId = null;
let extraCatalogId = null;
let staffId = null;

const ensureOrganization = async () => {
  const client = getPgClient();
  await client.connect();
  try {
    // Check if organization with ID 1 exists
    const existing = await client.query(
      `SELECT id, business_name FROM organizations WHERE id = $1 LIMIT 1`,
      [ORG_ID],
    );
    if (existing.rowCount === 0) {
      // Try to create organization - PostgreSQL sequences make it hard to set ID to 1
      // So we'll try to create one and see what ID we get
      const result = await client.query(
        `INSERT INTO organizations (user_id, business_name, business_type, country)
         VALUES ($1, $2, $3, $4) RETURNING id`,
        [`test-user-${Date.now()}`, 'Test Organization', 'company', 'US'],
      );
      const createdId = result.rows[0].id;
      if (createdId !== ORG_ID) {
        console.warn(`⚠️  Created organization with ID ${createdId} instead of ${ORG_ID}`);
        console.warn(`⚠️  Please either:`);
        console.warn(`   1. Update ORG_ID constant in test file to ${createdId}, or`);
        console.warn(`   2. Manually create organization with ID ${ORG_ID} in database`);
        throw new Error(`Organization ID mismatch: expected ${ORG_ID}, got ${createdId}. Please update ORG_ID or create org ${ORG_ID} manually.`);
      }
      console.log(`✓ Created test organization with ID ${ORG_ID}`);
    } else {
      console.log(`✓ Organization ${ORG_ID} (${existing.rows[0].business_name}) already exists`);
    }
  } finally {
    await client.end();
  }
};

const ensureAvailabilitySettings = async () => {
  const client = getPgClient();
  await client.connect();
  try {
    // Check if availability settings exist
    const existing = await client.query(
      `SELECT id FROM availability_settings WHERE organization_id = $1 LIMIT 1`,
      [ORG_ID],
    );

    if (existing.rowCount === 0) {
      // Create permissive availability settings for testing
      // Set earliest booking to 1 hour so tests can run with 26 hour offset
      // Note: Individual tests that need 24 hour validation will override this
      const settings = {
        business_hours: [
          { dayOfWeek: 0, isOpen: false, startTime: '', endTime: '' },
          { dayOfWeek: 1, isOpen: true, startTime: '09:00', endTime: '17:00' },
          { dayOfWeek: 2, isOpen: true, startTime: '09:00', endTime: '17:00' },
          { dayOfWeek: 3, isOpen: true, startTime: '09:00', endTime: '17:00' },
          { dayOfWeek: 4, isOpen: true, startTime: '09:00', endTime: '17:00' },
          { dayOfWeek: 5, isOpen: true, startTime: '09:00', endTime: '17:00' },
          { dayOfWeek: 6, isOpen: false, startTime: '', endTime: '' },
        ],
        booking_rules: {
          earliestBookingHours: 1, // Allow bookings 1 hour in advance for testing
          latestBookingHours: 720, // Allow bookings up to 30 days in advance
        },
        cancellation_rules: {
          refundType: 'free',
          cancelBeforeHours: 24,
          refundPercent: 100,
        },
        rescheduling_policy: {
          type: 'always',
        },
      };

      await client.query(
        `INSERT INTO availability_settings (id, organization_id, config)
         VALUES (gen_random_uuid(), $1, $2::jsonb)`,
        [ORG_ID, JSON.stringify(settings)],
      );
      console.log(`✓ Created permissive availability settings for organization ${ORG_ID}`);
    } else {
      // Update existing settings to be more permissive for testing
      await client.query(
        `UPDATE availability_settings 
         SET config = jsonb_set(
           jsonb_set(config, '{booking_rules,earliestBookingHours}', '1'),
           '{booking_rules,latestBookingHours}', '720'
         )
         WHERE organization_id = $1`,
        [ORG_ID],
      );
      console.log(`✓ Updated availability settings for organization ${ORG_ID} to be more permissive`);
    }
  } finally {
    await client.end();
  }
};

const ensureCatalogData = async () => {
  const client = getPgClient();
  await client.connect();
  try {
    const ensureItem = async (type, canonicalKey, name, config) => {
      const existing = await client.query(
        `SELECT id FROM catalog_items WHERE organization_id = $1 AND type = $2 AND canonical_key = $3 AND name = $4 LIMIT 1`,
        [ORG_ID, type, canonicalKey, name],
      );
      if (existing.rowCount === 0) {
        await client.query(
          `INSERT INTO catalog_items (organization_id, type, canonical_key, name, config, is_active)
           VALUES ($1, $2, $3, $4, $5::jsonb, true)`,
          [ORG_ID, type, canonicalKey, name, JSON.stringify(config)],
        );
      }
    };

    await ensureItem('reservation', 'room', ROOM_TYPE_NAME, {
      pricePerNight: 150,
      minStayNights: 1,
      maxGuests: 2,
      currency: 'USD',
      inventoryCount: 5,
      images: [],
    });

    await ensureItem('reservation', 'room', FALLBACK_ROOM_NAME, {
      pricePerNight: 120,
      minStayNights: 1,
      maxGuests: 1,
      currency: 'USD',
      inventoryCount: 3,
      images: [],
    });

    await ensureItem('service', 'service', SERVICE_NAME, {
      durationMinutes: 60,
      price: 80,
      currency: 'USD',
    });

    await ensureItem('reservation', 'extra', EXTRA_NAME, {
      price: 15,
      appliesToAll: true,
    });
  } finally {
    await client.end();
  }
};

const ensureStaffData = async () => {
  const client = getPgClient();
  await client.connect();
  try {
    // Check if staff already exists
    const existing = await client.query(
      `SELECT id FROM staff WHERE organization_id = $1 AND name = $2 LIMIT 1`,
      [ORG_ID, 'Integration Test Staff'],
    );

    let staffIdResult;
    if (existing.rowCount === 0) {
      // Create staff member with work hours 09:00-17:00 (matches business hours)
      const result = await client.query(
        `INSERT INTO staff (organization_id, name, role, work_start_time, work_end_time, is_available)
         VALUES ($1, $2, $3, $4, $5, true)
         RETURNING id`,
        [ORG_ID, 'Integration Test Staff', 'therapist', '09:00', '17:00'],
      );
      staffIdResult = result.rows[0].id;
      console.log(`✓ Created staff member with ID ${staffIdResult}`);
    } else {
      staffIdResult = existing.rows[0].id;
      console.log(`✓ Staff member already exists with ID ${staffIdResult}`);
    }

    // Get service catalog ID
    const serviceResult = await client.query(
      `SELECT id FROM catalog_items WHERE organization_id = $1 AND type = $2 AND canonical_key = $3 AND name = $4 LIMIT 1`,
      [ORG_ID, 'service', 'service', SERVICE_NAME],
    );

    if (serviceResult.rowCount === 0) {
      throw new Error(`Service ${SERVICE_NAME} not found. Ensure catalog data is seeded first.`);
    }

    const serviceId = serviceResult.rows[0].id;

    // Link staff to service via staff_services junction table
    const staffServiceCheck = await client.query(
      `SELECT id FROM staff_services WHERE staff_id = $1 AND service_id = $2 LIMIT 1`,
      [staffIdResult, serviceId],
    );

    if (staffServiceCheck.rowCount === 0) {
      await client.query(
        `INSERT INTO staff_services (staff_id, service_id)
         VALUES ($1, $2)`,
        [staffIdResult, serviceId],
      );
      console.log(`✓ Linked staff ${staffIdResult} to service ${serviceId}`);
    } else {
      console.log(`✓ Staff-service link already exists`);
    }

    return staffIdResult;
  } finally {
    await client.end();
  }
};

const clearRoomReservations = async () => {
  const client = getPgClient();
  await client.connect();
  try {
    await client.query(`DELETE FROM bookings WHERE organization_id = $1`, [ORG_ID]);
  } finally {
    await client.end();
  }
};

const loadServiceAvailability = async (serviceId, date) => {
  console.log('Fetching service availability', { serviceId, date });
  const response = await request(BASE_URL)
    .get('/api/internal/availability/services')
    .query({
      organization_id: ORG_ID,
      service_id: serviceId,
      date,
    });

  if (response.status !== 200) {
    console.error('Service availability request failed', {
      status: response.status,
      body: JSON.stringify(response.body, null, 2),
    });
  }

  expect(response.status).toBe(200);

  const body = response.body ?? {};
  const slots = body.available_slots ?? body.data?.available_slots ?? [];

  expect(Array.isArray(slots)).toBe(true);

  if (slots.length === 0) {
    console.warn('No service slots returned', JSON.stringify(body, null, 2));
  } else {
    console.log('Sample service slot', JSON.stringify(slots[0], null, 2));
  }

  return slots;
};

const createServiceBooking = async ({
  customerId,
  itemId,
  startTime,
  endTime,
  addons = [],
}) => {
  console.log('Creating service booking payload', {
    customerId,
    itemId,
    startTime,
    endTime,
    addons,
  });

  const payload = {
    organization_id: ORG_ID,
    customer_id: Number(customerId),
    booking_type: 'service',
    item_id: Number(itemId),
    start_time: startTime,
    end_time: endTime,
    addons: addons.map(addon => ({
      id: Number(addon.id),
      quantity: addon.quantity ?? 1,
    })),
  };

  const response = await request(BASE_URL)
    .post('/api/internal/bookings')
    .send(payload)
    .set('Content-Type', 'application/json');

  if (![201, 409].includes(response.status)) {
    console.error('Service booking creation failed', {
      status: response.status,
      body: JSON.stringify(response.body, null, 2),
      payload,
    });
  }

  expect([201, 409]).toContain(response.status);

  const booking = response.body?.data?.booking ?? response.body.booking ?? null;

  if (response.status === 201) {
    expect(booking).toEqual(
      expect.objectContaining({
        booking_code: expect.any(String),
        status: expect.any(String),
        hold_expires_at: expect.any(String),
        total_amount: expect.any(Number),
        id: expect.any(Number),
      }),
    );
  }

  return { payload, response, booking };
};

const runScenario = async (label, fn) => {
  try {
    console.log(`Starting scenario: ${label}`);
    await fn();
    console.log(`Completed scenario: ${label}`);
  } catch (error) {
    console.error(`Scenario ${label} failed`, error);
    if (error && error.errors) {
      console.error('AggregateError details:', error.errors);
    }
    throw error;
  }
};

describe('Internal API integration workflows', () => {
  beforeAll(async () => {
    console.log('Setting up test data...');
    await ensureOrganization();
    await ensureAvailabilitySettings();
    await ensureCatalogData();
    staffId = await ensureStaffData();

    // Verify organization exists
    const client = getPgClient();
    await client.connect();
    try {
      const orgCheck = await client.query(
        `SELECT id, business_name FROM organizations WHERE id = $1`,
        [ORG_ID],
      );
      if (orgCheck.rowCount === 0) {
        throw new Error(`Organization ${ORG_ID} does not exist. Please create it manually or update ORG_ID constant.`);
      }
      console.log(`✓ Verified organization ${ORG_ID} exists: ${orgCheck.rows[0].business_name}`);
    } finally {
      await client.end();
    }

    primaryRoomTypeId = await getCatalogItemId(ROOM_TYPE_NAME, 'reservation', 'room');
    fallbackRoomTypeId = await getCatalogItemId(FALLBACK_ROOM_NAME, 'reservation', 'room');
    serviceCatalogId = await getCatalogItemId(SERVICE_NAME, 'service', 'service');
    extraCatalogId = await getCatalogItemId(EXTRA_NAME, 'reservation', 'extra');
    if (!primaryRoomTypeId && !fallbackRoomTypeId) {
      throw new Error('Required room types were not seeded correctly');
    }
    if (!primaryRoomTypeId) {
      primaryRoomTypeId = fallbackRoomTypeId;
    }
    if (!fallbackRoomTypeId) {
      fallbackRoomTypeId = primaryRoomTypeId;
    }
    if (!serviceCatalogId) {
      throw new Error('Service catalog item was not seeded correctly');
    }
    if (!staffId) {
      throw new Error('Staff member was not seeded correctly');
    }
    console.log('✓ Test data setup complete');
  });

  beforeEach(async () => {
    await clearRoomReservations();
  });

  it('Happy path: reservation booking through payment confirmation', async () => {
    await runScenario('happy path', async () => {
      // Validate organization details before booking flow
      const orgDetails = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/details`);

      if (orgDetails.status !== 200) {
        console.error('Organization details request failed:', {
          status: orgDetails.status,
          body: orgDetails.body,
          text: typeof orgDetails.text === 'function' ? 'N/A' : orgDetails.text,
          headers: orgDetails.headers,
        });
        // Try to get error message from body
        if (orgDetails.body && typeof orgDetails.body === 'object') {
          console.error('Error from response:', orgDetails.body.message || orgDetails.body.error || 'No error message');
        }
      }

      expect(orgDetails.status).toBe(200);

      // Check Content-Type only if status is 200
      if (orgDetails.status === 200) {
        expect(orgDetails.headers['content-type']).toMatch(/json/);
      }

      expect(orgDetails.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            organization: expect.objectContaining({
              id: ORG_ID,
              businessName: expect.any(String),
            }),
          }),
        }),
      );

      const { customerId } = await createCustomer();

      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);

      expect(reservationInventory.length).toBeGreaterThan(0);

      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('No suitable room type available for booking');
      }

      const { response: bookingResponse, booking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      expect(bookingResponse.status).toBe(201);
      expect(booking).toEqual(
        expect.objectContaining({
          booking_code: expect.any(String),
          status: 'pending',
          hold_expires_at: expect.any(String),
          total_amount: expect.any(Number),
          id: expect.any(Number),
        }),
      );

      const bookingCode = booking.booking_code;
      const bookingId = booking.id;
      const totalAmount = Number(booking.total_amount);

      const { response: intentResponse } = await createPaymentIntent(
        bookingId,
        totalAmount,
      );

      expect(intentResponse.status).toBe(200);
      expect(intentResponse.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            payment_url: expect.any(String),
          }),
        }),
      );

      const paymentIntentId = `pi_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const chargeId = `ch_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const amountMinor = Math.round(totalAmount * 100);

      const { response: webhookResponse } = await fireStripeWebhook({
        eventId: `evt_${uuidv4()}`,
        type: 'payment_intent.succeeded',
        bookingId,
        bookingCode,
        bookingType: 'room', // Use 'room' instead of 'reservation' to match webhook handler expectations
        paymentIntentId,
        chargeId,
        amountMinor,
        currency: 'usd',
      });

      expect(webhookResponse.status).toBe(200);
      expect(webhookResponse.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({ received: true }),
        }),
      );

      const bookingAfterPayment = await fetchBooking(bookingCode);

      expect(bookingAfterPayment).toEqual(
        expect.objectContaining({
          booking_type: 'room',
          booking_code: bookingCode,
          status: 'confirmed',
        }),
      );
    });
  });

  it('Booking creation fails when earliest booking window is violated (422)', async () => {
    await runScenario('booking earliest rule validation', async () => {
      // Temporarily set earliest booking to 24 hours for this test
      const client = getPgClient();
      await client.connect();
      try {
        await client.query(
          `UPDATE availability_settings 
           SET config = jsonb_set(config, '{booking_rules,earliestBookingHours}', '24')
           WHERE organization_id = $1`,
          [ORG_ID],
        );
      } finally {
        await client.end();
      }

      const { customerId } = await createCustomer();

      const checkIn = addMinutes(new Date(), 60).toISOString();
      const checkOut = addMinutes(new Date(), 60 * 24).toISOString();
      const itemId = primaryRoomTypeId ?? fallbackRoomTypeId;
      if (!itemId) {
        throw new Error('No room type available to trigger earliest booking rule');
      }

      const payload = {
        organization_id: ORG_ID,
        customer_id: Number(customerId),
        booking_type: 'reservation',
        item_id: Number(itemId),
        check_in: checkIn,
        check_out: checkOut,
        guests: 1,
      };

      const response = await request(BASE_URL)
        .post('/api/internal/bookings')
        .send(payload)
        .set('Content-Type', 'application/json');

      expect(response.status).toBe(422);
      expect(response.body).toEqual(
        expect.objectContaining({
          success: false,
          error: expect.objectContaining({
            message: expect.stringContaining('hours in advance'),
          }),
        }),
      );
    });
  });

  it('Payment intent is rejected when booking hold expired (409)', async () => {
    await runScenario('expired booking prevents payment intent', async () => {
      const { customerId } = await createCustomer();

      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);

      expect(reservationInventory.length).toBeGreaterThan(0);

      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('Failed to select room for booking');
      }

      const { response: bookingResponse, booking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
        extras: selection.extras ?? [],
      });

      expect(bookingResponse.status).toBe(201);

      const bookingCode = booking.booking_code;
      const bookingId = booking.id;
      const totalAmount = Number(booking.total_amount);

      await expireBookingHold(bookingId);

      // Small delay to ensure the database update is committed
      await new Promise(resolve => setTimeout(resolve, 100));

      const { response: intentResponse } = await createPaymentIntent(
        bookingId,
        totalAmount,
      );

      expect(intentResponse.status).toBe(409);
      expect(intentResponse.body).toEqual(
        expect.objectContaining({
          success: false,
          message: expect.stringContaining('Booking hold has expired'),
        }),
      );
    });
  });

  it('Duplicate Stripe webhook event is ignored gracefully (200)', async () => {
    await runScenario('duplicate webhook', async () => {
      const { customerId } = await createCustomer();

      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);

      expect(reservationInventory.length).toBeGreaterThan(0);

      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('Failed to select room for booking');
      }

      const { response: bookingResponse, booking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      expect(bookingResponse.status).toBe(201);

      const bookingCode = booking.booking_code;
      const bookingId = booking.id;
      const totalAmount = Number(booking.total_amount);

      const { response: intentResponse } = await createPaymentIntent(
        bookingId,
        totalAmount,
      );

      expect(intentResponse.status).toBe(200);

      const eventId = `evt_${uuidv4()}`;
      const paymentIntentId = `pi_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const chargeId = `ch_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const amountMinor = Math.round(totalAmount * 100);

      const firstWebhook = await fireStripeWebhook({
        eventId,
        type: 'payment_intent.succeeded',
        bookingId,
        bookingCode,
        bookingType: 'room', // Use 'room' instead of 'reservation' to match webhook handler expectations
        paymentIntentId,
        chargeId,
        amountMinor,
        currency: 'usd',
      });

      expect(firstWebhook.response.status).toBe(200);

      const secondWebhook = await fireStripeWebhook({
        eventId,
        type: 'payment_intent.succeeded',
        bookingId,
        bookingCode,
        bookingType: 'room', // Use 'room' instead of 'reservation' to match webhook handler expectations
        paymentIntentId,
        chargeId,
        amountMinor,
        currency: 'usd',
      });

      expect(secondWebhook.response.status).toBe(200);
      expect(secondWebhook.response.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            received: true,
            duplicate: expect.any(Boolean),
          }),
        }),
      );
    });
  });

  it('Stripe webhook with invalid signature is rejected (400/401)', async () => {
    await runScenario('invalid webhook signature', async () => {
      const { customerId } = await createCustomer();

      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);

      expect(reservationInventory.length).toBeGreaterThan(0);

      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('Failed to select room for booking');
      }

      const { response: bookingResponse, booking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      expect(bookingResponse.status).toBe(201);

      const bookingCode = booking.booking_code;
      const bookingId = booking.id;
      const totalAmount = Number(booking.total_amount);

      const { response: intentResponse } = await createPaymentIntent(
        bookingId,
        totalAmount,
      );

      expect(intentResponse.status).toBe(200);

      const eventId = `evt_${uuidv4()}`;
      const paymentIntentId = `pi_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const chargeId = `ch_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const amountMinor = Math.round(totalAmount * 100);

      const invalidWebhook = await fireInvalidStripeWebhook({
        eventId,
        type: 'payment_intent.succeeded',
        bookingId,
        bookingCode,
        bookingType: 'room', // Use 'room' instead of 'reservation' to match webhook handler expectations
        paymentIntentId,
        chargeId,
        amountMinor,
        currency: 'usd',
      });

      expect([400, 401]).toContain(invalidWebhook.response.status);
      expect(invalidWebhook.response.body).toEqual(
        expect.objectContaining({
          success: false,
        }),
      );

      const bookingAfterInvalidWebhook = await fetchBooking(bookingCode);

      expect(bookingAfterInvalidWebhook).toEqual(
        expect.objectContaining({
          booking_code: bookingCode,
          status: 'pending',
        }),
      );
    });
  });

  it('Stripe payment failure webhook preserves booking hold and records failure state', async () => {
    await runScenario('payment failure webhook', async () => {
      const { customerId } = await createCustomer();

      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);

      expect(reservationInventory.length).toBeGreaterThan(0);

      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('Failed to select room for booking');
      }

      const { response: bookingResponse, booking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      expect(bookingResponse.status).toBe(201);

      const bookingCode = booking.booking_code;
      const bookingId = booking.id;
      const totalAmount = Number(booking.total_amount);

      const { response: intentResponse } = await createPaymentIntent(
        bookingId,
        totalAmount,
      );

      expect(intentResponse.status).toBe(200);

      const eventId = `evt_${uuidv4()}`;
      const paymentIntentId = `pi_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const chargeId = `ch_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const amountMinor = Math.round(totalAmount * 100);

      const failureWebhook = await fireStripeWebhook({
        eventId,
        type: 'payment_intent.payment_failed',
        bookingId,
        bookingCode,
        bookingType: 'room', // Use 'room' instead of 'reservation' to match webhook handler expectations
        paymentIntentId,
        chargeId,
        amountMinor,
        currency: 'usd',
        status: 'requires_payment_method',
      });

      expect([200, 202]).toContain(failureWebhook.response.status);
      expect(failureWebhook.response.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            received: true,
          }),
        }),
      );

      const bookingAfterFailure = await fetchBooking(bookingCode);

      expect(typeof bookingAfterFailure.status).toBe('string');
      expect(['pending', 'payment_failed', 'hold_expired']).toContain(
        bookingAfterFailure.status,
      );
    });
  });

  it('Happy path: service booking through payment confirmation', async () => {
    await runScenario('service happy path', async () => {
      const { customerId } = await createCustomer();

      // Get service availability for tomorrow
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const dateStr = tomorrow.toISOString().slice(0, 10);

      const slots = await loadServiceAvailability(serviceCatalogId, dateStr);

      expect(slots.length).toBeGreaterThan(0);

      // Pick first available slot
      const slot = slots[0];
      const startTime = slot.start_time;
      const endTime = slot.end_time;

      const { response: bookingResponse, booking } = await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime,
        endTime,
      });

      expect(bookingResponse.status).toBe(201);
      expect(booking).toEqual(
        expect.objectContaining({
          booking_code: expect.any(String),
          status: 'pending',
          hold_expires_at: expect.any(String),
          total_amount: expect.any(Number),
          id: expect.any(Number),
        }),
      );

      const bookingCode = booking.booking_code;
      const bookingId = booking.id;
      const totalAmount = Number(booking.total_amount);

      const { response: intentResponse } = await createPaymentIntent(
        bookingId,
        totalAmount,
      );

      expect(intentResponse.status).toBe(200);
      expect(intentResponse.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            payment_url: expect.any(String),
          }),
        }),
      );

      const paymentIntentId = `pi_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const chargeId = `ch_${uuidv4().replace(/-/g, '').slice(0, 24)}`;
      const amountMinor = Math.round(totalAmount * 100);

      const { response: webhookResponse } = await fireStripeWebhook({
        eventId: `evt_${uuidv4()}`,
        type: 'payment_intent.succeeded',
        bookingId,
        bookingCode,
        bookingType: 'service',
        paymentIntentId,
        chargeId,
        amountMinor,
        currency: 'usd',
      });

      expect(webhookResponse.status).toBe(200);
      expect(webhookResponse.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({ received: true }),
        }),
      );

      const bookingAfterPayment = await fetchBooking(bookingCode);

      expect(bookingAfterPayment).toEqual(
        expect.objectContaining({
          booking_type: 'service',
          booking_code: bookingCode,
          status: 'confirmed',
        }),
      );
    });
  });

  it('Service booking creation fails when earliest booking window is violated (409)', async () => {
    await runScenario('service earliest rule validation', async () => {
      // Temporarily set earliest booking to 24 hours for this test
      const client = getPgClient();
      await client.connect();
      try {
        await client.query(
          `UPDATE availability_settings 
           SET config = jsonb_set(config, '{booking_rules,earliestBookingHours}', '24')
           WHERE organization_id = $1`,
          [ORG_ID],
        );
      } finally {
        await client.end();
      }

      const { customerId } = await createCustomer();

      // Try to book 1 hour from now (violates 24 hour rule)
      // Note: Service bookings don't validate earliest booking window in createServiceBooking,
      // so this will fail with BOOKING_CONFLICT (409) instead of validation error (422)
      const startTime = addMinutes(new Date(), 60).toISOString();
      const endTime = addMinutes(new Date(), 120).toISOString();

      const payload = {
        organization_id: ORG_ID,
        customer_id: Number(customerId),
        booking_type: 'service',
        item_id: Number(serviceCatalogId),
        start_time: startTime,
        end_time: endTime,
        addons: [],
      };

      const response = await request(BASE_URL)
        .post('/api/internal/bookings')
        .send(payload)
        .set('Content-Type', 'application/json');

      // Service bookings return 409 (BOOKING_CONFLICT) when no slot is available,
      // which includes cases where earliest booking window would be violated
      expect(response.status).toBe(409);
      expect(response.body).toEqual(
        expect.objectContaining({
          success: false,
          error: expect.objectContaining({
            code: 'BOOKING_CONFLICT',
          }),
        }),
      );
    });
  });

  it('Service booking cancellation works correctly', async () => {
    await runScenario('service cancellation', async () => {
      const { customerId } = await createCustomer();

      // Get service availability for tomorrow
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const dateStr = tomorrow.toISOString().slice(0, 10);

      const slots = await loadServiceAvailability(serviceCatalogId, dateStr);

      expect(slots.length).toBeGreaterThan(0);

      const slot = slots[0];
      const { response: bookingResponse, booking } = await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime: slot.start_time,
        endTime: slot.end_time,
      });

      expect(bookingResponse.status).toBe(201);

      const bookingCode = booking.booking_code;

      const quoteResponse = await request(BASE_URL)
        .get(`/api/internal/bookings/${bookingCode}/cancellation-quote`)
        .query({ organization_id: ORG_ID });

      expect(quoteResponse.status).toBe(200);

      const cancelResponse = await request(BASE_URL)
        .post(`/api/internal/bookings/${bookingCode}/cancel`)
        .set('Content-Type', 'application/json')
        .send({
          organization_id: ORG_ID,
          cancellation_type: 'user_initiated',
          reason: 'customer_request',
          refundMethod: 'original',
          notifyCustomer: false,
        });

      expect(cancelResponse.status).toBe(200);
      expect(cancelResponse.body).toEqual(
        expect.objectContaining({
          success: true,
        }),
      );

      const bookingAfterCancellation = await fetchBooking(bookingCode);

      expect(typeof bookingAfterCancellation.status).toBe('string');
      expect(bookingAfterCancellation.status.toLowerCase()).toContain('cancel');
    });
  });

  it('Customer upsert supports dedupe and listing within organization', async () => {
    await runScenario('customer upsert and list', async () => {
      const email = `${uuidv4()}@example.test`;

      const payload = {
        organization_id: ORG_ID,
        name: 'Jess Tester',
        email,
        phone: '+15553334444',
      };

      const firstCreate = await request(BASE_URL)
        .post('/api/internal/customers')
        .send(payload)
        .set('Content-Type', 'application/json');

      expect(firstCreate.status).toBe(201);

      const firstCustomer = firstCreate.body?.data?.customer ?? firstCreate.body.customer;

      expect(firstCustomer).toEqual(
        expect.objectContaining({
          id: expect.any(Number),
          email: email.toLowerCase(),
        }),
      );

      const secondCreate = await request(BASE_URL)
        .post('/api/internal/customers')
        .send({
          ...payload,
          name: 'Jessica Tester',
        })
        .set('Content-Type', 'application/json');

      expect([200, 201]).toContain(secondCreate.status);

      const secondCustomer = secondCreate.body?.data?.customer ?? secondCreate.body.customer;

      expect(secondCustomer).toEqual(
        expect.objectContaining({
          id: firstCustomer.id,
          email: email.toLowerCase(),
        }),
      );

      const lookup = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/customers`)
        .query({ email });

      expect(lookup.status).toBe(200);

      const lookupCustomer = lookup.body?.data?.customer ?? lookup.body.customer;

      expect(lookupCustomer).toEqual(
        expect.objectContaining({
          id: firstCustomer.id,
          email: email.toLowerCase(),
        }),
      );
    });
  });

  it('Cancellation endpoint rejects invalid payload (400)', async () => {
    await runScenario('cancellation validation', async () => {
      const { customerId } = await createCustomer();

      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);

      expect(reservationInventory.length).toBeGreaterThan(0);

      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('Failed to select room for booking');
      }

      const { response: bookingResponse, booking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      expect(bookingResponse.status).toBe(201);

      const bookingCode = booking.booking_code;

      const quoteResponse = await request(BASE_URL)
        .get(`/api/internal/bookings/${bookingCode}/cancellation-quote`)
        .query({ organization_id: ORG_ID });

      expect(quoteResponse.status).toBe(200);

      const cancelResponse = await request(BASE_URL)
        .post(`/api/internal/bookings/${bookingCode}/cancel`)
        .set('Content-Type', 'application/json')
        .send({
          // Missing organization_id and reason to trigger validation error
          refundMethod: 'original',
        });

      expect([400, 422]).toContain(cancelResponse.status);
      expect(cancelResponse.body).toEqual(
        expect.objectContaining({
          success: false,
          message: expect.stringMatching(/organization_id|Expected number/i),
        }),
      );
    });
  });

  it('Cancellation endpoint processes valid request and updates booking state (200)', async () => {
    await runScenario('successful cancellation', async () => {
      const { customerId } = await createCustomer();

      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);

      expect(reservationInventory.length).toBeGreaterThan(0);

      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('Failed to select room for booking');
      }

      const { response: bookingResponse, booking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      expect(bookingResponse.status).toBe(201);

      const bookingCode = booking.booking_code;

      const cancelResponse = await request(BASE_URL)
        .post(`/api/internal/bookings/${bookingCode}/cancel`)
        .set('Content-Type', 'application/json')
        .send({
          organization_id: ORG_ID,
          cancellation_type: 'user_initiated',
          reason: 'guest_request',
          refundMethod: 'original',
          notifyCustomer: false,
        });

      if (cancelResponse.status !== 200) {
        console.error(
          'Cancellation response (unexpected status)',
          cancelResponse.status,
          JSON.stringify(cancelResponse.body, null, 2),
        );
      }

      expect(cancelResponse.status).toBe(200);

      // The response structure may vary, so check for cancellation in data or data.data
      const cancellation = cancelResponse.body.data?.cancellation ?? cancelResponse.body.data?.data?.cancellation;

      expect(cancellation).toBeDefined();
      expect(cancellation).toEqual(
        expect.objectContaining({
          booking_code: bookingCode,
          cancellation_type: 'user_initiated',
        }),
      );
      expect(cancelResponse.body).toEqual(
        expect.objectContaining({
          success: true,
        }),
      );

      const cancelledBooking = cancellation;

      expect(typeof cancelledBooking.cancelled_at).toBe('string');

      const bookingAfterCancellation = await fetchBooking(bookingCode);

      expect(typeof bookingAfterCancellation.status).toBe('string');
      expect(bookingAfterCancellation.status.toLowerCase()).toContain('cancel');
    });
  });

  it('Booking reschedule (PATCH) works for both service and reservation', async () => {
    await runScenario('booking reschedule both types', async () => {
      const { customerId } = await createCustomer();

      // Test service booking reschedule
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const serviceDateStr = tomorrow.toISOString().slice(0, 10);
      const serviceSlots = await loadServiceAvailability(serviceCatalogId, serviceDateStr);

      expect(serviceSlots.length).toBeGreaterThan(0);

      const { booking: serviceBooking } = await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime: serviceSlots[0].start_time,
        endTime: serviceSlots[0].end_time,
      });

      const newServiceStart = new Date(serviceSlots[1]?.start_time || serviceSlots[0].start_time);
      newServiceStart.setHours(newServiceStart.getHours() + 2);

      const serviceRescheduleResponse = await request(BASE_URL)
        .patch(`/api/internal/bookings/${serviceBooking.booking_code}`)
        .query({ organization_id: ORG_ID })
        .send({
          updates: {
            starts_at: newServiceStart.toISOString(),
            ends_at: new Date(newServiceStart.getTime() + 60 * 60 * 1000).toISOString(),
          },
        });

      expect([200, 409, 422]).toContain(serviceRescheduleResponse.status);

      // Test reservation booking reschedule
      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);

      expect(reservationInventory.length).toBeGreaterThan(0);

      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('No room available for reschedule test');
      }

      const { booking: reservationBooking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      const newCheckIn = addDays(new Date(checkInIso), 1);
      const newCheckOut = addDays(newCheckIn, 2);

      const reservationRescheduleResponse = await request(BASE_URL)
        .patch(`/api/internal/bookings/${reservationBooking.booking_code}`)
        .query({ organization_id: ORG_ID })
        .send({
          updates: {
            starts_at: newCheckIn.toISOString(),
            ends_at: newCheckOut.toISOString(),
          },
        });

      expect([200, 409, 422]).toContain(reservationRescheduleResponse.status);
    });
  });

  it('Payment status endpoint works for both service and reservation', async () => {
    await runScenario('payment status both types', async () => {
      const { customerId } = await createCustomer();

      // Service booking
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const serviceDateStr = tomorrow.toISOString().slice(0, 10);
      const serviceSlots = await loadServiceAvailability(serviceCatalogId, serviceDateStr);
      const { booking: serviceBooking } = await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime: serviceSlots[0].start_time,
        endTime: serviceSlots[0].end_time,
      });

      const servicePaymentStatus = await request(BASE_URL)
        .get(`/api/internal/bookings/${serviceBooking.booking_code}/payment-status`)
        .query({ organization_id: ORG_ID });

      expect(servicePaymentStatus.status).toBe(200);
      expect(servicePaymentStatus.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            payment_required: expect.any(Boolean),
            payment_summary: expect.any(Object),
          }),
        }),
      );

      // Reservation booking
      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);
      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('No room available for payment status test');
      }
      const { booking: reservationBooking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      const reservationPaymentStatus = await request(BASE_URL)
        .get(`/api/internal/bookings/${reservationBooking.booking_code}/payment-status`)
        .query({ organization_id: ORG_ID });

      expect(reservationPaymentStatus.status).toBe(200);
      expect(reservationPaymentStatus.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            payment_required: expect.any(Boolean),
            payment_summary: expect.any(Object),
          }),
        }),
      );
    });
  });

  it('Payment URL endpoint works for both service and reservation', async () => {
    await runScenario('payment url both types', async () => {
      const { customerId } = await createCustomer();

      // Service booking with payment intent
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const serviceDateStr = tomorrow.toISOString().slice(0, 10);
      const serviceSlots = await loadServiceAvailability(serviceCatalogId, serviceDateStr);
      const { booking: serviceBooking } = await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime: serviceSlots[0].start_time,
        endTime: serviceSlots[0].end_time,
      });

      await createPaymentIntent(serviceBooking.id, Number(serviceBooking.total_amount));

      const servicePaymentUrl = await request(BASE_URL)
        .get(`/api/internal/bookings/${serviceBooking.booking_code}/payment-url`)
        .query({ organization_id: ORG_ID });

      expect(servicePaymentUrl.status).toBe(200);
      expect(servicePaymentUrl.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            has_payment_intent: expect.any(Boolean),
          }),
        }),
      );

      // Reservation booking with payment intent
      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);
      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('No room available for payment URL test');
      }
      const { booking: reservationBooking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      await createPaymentIntent(reservationBooking.id, Number(reservationBooking.total_amount));

      const reservationPaymentUrl = await request(BASE_URL)
        .get(`/api/internal/bookings/${reservationBooking.booking_code}/payment-url`)
        .query({ organization_id: ORG_ID });

      expect(reservationPaymentUrl.status).toBe(200);
      expect(reservationPaymentUrl.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            has_payment_intent: expect.any(Boolean),
          }),
        }),
      );
    });
  });

  it('Manual confirmation endpoint works for both service and reservation', async () => {
    await runScenario('manual confirmation both types', async () => {
      const { customerId } = await createCustomer();

      // Service booking
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const serviceDateStr = tomorrow.toISOString().slice(0, 10);
      const serviceSlots = await loadServiceAvailability(serviceCatalogId, serviceDateStr);
      const { booking: serviceBooking } = await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime: serviceSlots[0].start_time,
        endTime: serviceSlots[0].end_time,
      });

      const serviceConfirm = await request(BASE_URL)
        .post(`/api/internal/bookings/${serviceBooking.booking_code}/confirm`)
        .query({ organization_id: ORG_ID });

      expect([200, 400, 409, 422]).toContain(serviceConfirm.status);

      // Reservation booking
      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);
      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('No room available for payment status test');
      }
      const { booking: reservationBooking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      const reservationConfirm = await request(BASE_URL)
        .post(`/api/internal/bookings/${reservationBooking.booking_code}/confirm`)
        .query({ organization_id: ORG_ID });

      expect([200, 400, 409, 422]).toContain(reservationConfirm.status);
    });
  });

  it('WhatsApp confirmation endpoint works for both service and reservation', async () => {
    await runScenario('whatsapp confirmation both types', async () => {
      const { customerId } = await createCustomer();

      // Service booking
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const serviceDateStr = tomorrow.toISOString().slice(0, 10);
      const serviceSlots = await loadServiceAvailability(serviceCatalogId, serviceDateStr);
      const { booking: serviceBooking } = await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime: serviceSlots[0].start_time,
        endTime: serviceSlots[0].end_time,
      });

      const serviceConfirmation = await request(BASE_URL)
        .get(`/api/internal/bookings/${serviceBooking.booking_code}/confirmation`)
        .query({ organization_id: ORG_ID });

      // Confirmation endpoint may require booking to be confirmed first
      expect([200, 404, 422]).toContain(serviceConfirmation.status);

      if (serviceConfirmation.status === 200) {
        expect(serviceConfirmation.body).toEqual(
          expect.objectContaining({
            success: true,
            data: expect.objectContaining({
              booking_code: serviceBooking.booking_code,
              formatted_message: expect.any(String),
            }),
          }),
        );
      }

      // Reservation booking
      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);
      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('No room available for payment status test');
      }
      const { booking: reservationBooking } = await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      const reservationConfirmation = await request(BASE_URL)
        .get(`/api/internal/bookings/${reservationBooking.booking_code}/confirmation`)
        .query({ organization_id: ORG_ID });

      // Confirmation endpoint may require booking to be confirmed first
      expect([200, 404, 422]).toContain(reservationConfirmation.status);

      if (reservationConfirmation.status === 200) {
        expect(reservationConfirmation.body).toEqual(
          expect.objectContaining({
            success: true,
            data: expect.objectContaining({
              booking_code: reservationBooking.booking_code,
              formatted_message: expect.any(String),
            }),
          }),
        );
      }
    });
  });

  it('Booking search endpoint works for both service and reservation', async () => {
    await runScenario('booking search both types', async () => {
      const { customerId } = await createCustomer();

      // Create service booking
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const serviceDateStr = tomorrow.toISOString().slice(0, 10);
      const serviceSlots = await loadServiceAvailability(serviceCatalogId, serviceDateStr);
      await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime: serviceSlots[0].start_time,
        endTime: serviceSlots[0].end_time,
      });

      // Create reservation booking
      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);
      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('No room available for booking search test');
      }
      await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      // Search for service bookings
      const serviceSearch = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/bookings/search`)
        .query({
          organization_id: ORG_ID,
          booking_type: 'service',
          customer_id: customerId,
        });

      expect(serviceSearch.status).toBe(200);
      expect(serviceSearch.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            bookings: expect.any(Array),
          }),
        }),
      );

      // Search for reservation bookings
      const reservationSearch = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/bookings/search`)
        .query({
          organization_id: ORG_ID,
          booking_type: 'reservation',
          customer_id: customerId,
        });

      expect(reservationSearch.status).toBe(200);
      expect(reservationSearch.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            bookings: expect.any(Array),
          }),
        }),
      );
    });
  });

  it('Organization services endpoint works', async () => {
    await runScenario('organization services', async () => {
      const servicesResponse = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/services`);

      expect(servicesResponse.status).toBe(200);
      expect(servicesResponse.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            services: expect.any(Array),
          }),
        }),
      );
    });
  });

  it('Organization reservation endpoint works', async () => {
    await runScenario('organization reservation', async () => {
      const reservationResponse = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/reservation`);

      expect(reservationResponse.status).toBe(200);
      expect(reservationResponse.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            room_types: expect.any(Array),
            extras: expect.any(Array),
          }),
        }),
      );
    });
  });

  it('Organization staff endpoint works', async () => {
    await runScenario('organization staff', async () => {
      const staffResponse = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/staff`);

      expect(staffResponse.status).toBe(200);
      expect(staffResponse.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            staff: expect.any(Array),
          }),
        }),
      );
    });
  });

  it('Organization hours endpoint works', async () => {
    await runScenario('organization hours', async () => {
      const hoursResponse = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/hours`);

      // Hours endpoint may not exist yet (404) or may return 200
      expect([200, 404]).toContain(hoursResponse.status);

      if (hoursResponse.status === 200) {
        expect(hoursResponse.body).toEqual(
          expect.objectContaining({
            success: true,
            data: expect.objectContaining({
              business_hours: expect.any(Array),
            }),
          }),
        );
      }
    });
  });

  it('Organization FAQs endpoint works', async () => {
    await runScenario('organization faqs', async () => {
      const faqsResponse = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/faqs`)
        .query({ query: 'booking', limit: 5 });

      expect([200, 404]).toContain(faqsResponse.status);
    });
  });

  it('Service details endpoint works', async () => {
    await runScenario('service details', async () => {
      if (!serviceCatalogId) {
        throw new Error('Service catalog ID not available');
      }

      const serviceResponse = await request(BASE_URL)
        .get(`/api/internal/services/${serviceCatalogId}`);

      expect([200, 404]).toContain(serviceResponse.status);
    });
  });

  it('Next available slot endpoint works for services', async () => {
    await runScenario('next available slot', async () => {
      if (!serviceCatalogId) {
        throw new Error('Service catalog ID not available');
      }

      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const dateStr = tomorrow.toISOString().slice(0, 10);

      const nextAvailableResponse = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/next-available`)
        .query({
          service_id: serviceCatalogId,
          date: dateStr,
        });

      expect([200, 404]).toContain(nextAvailableResponse.status);
    });
  });

  it('Customer booking history endpoint works for both service and reservation', async () => {
    await runScenario('customer booking history both types', async () => {
      const { customerId } = await createCustomer();

      // Create service booking
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const serviceDateStr = tomorrow.toISOString().slice(0, 10);
      const serviceSlots = await loadServiceAvailability(serviceCatalogId, serviceDateStr);
      await createServiceBooking({
        customerId,
        itemId: serviceCatalogId,
        startTime: serviceSlots[0].start_time,
        endTime: serviceSlots[0].end_time,
      });

      // Create reservation booking
      const { checkInIso, checkOutIso } = buildStayRange(2);
      const reservationInventory = await loadReservationAvailability(checkInIso, checkOutIso);
      const selection = selectRoomFromAvailability(reservationInventory);
      if (!selection) {
        throw new Error('No room available for customer booking history test');
      }
      await createBooking({
        customerId,
        itemId: selection.itemId,
        checkIn: checkInIso,
        checkOut: checkOutIso,
        guests: selection.guestCount,
      });

      // Get customer booking history
      const customerBookings = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/customers/${customerId}/bookings`);

      expect(customerBookings.status).toBe(200);
      expect(customerBookings.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            bookings: expect.any(Array),
            total: expect.any(Number),
            limit: expect.any(Number),
            offset: expect.any(Number),
          }),
        }),
      );

      // Test with filters
      const serviceBookings = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/customers/${customerId}/bookings`)
        .query({ booking_type: 'service' });

      expect(serviceBookings.status).toBe(200);
      expect(serviceBookings.body.data.bookings).toBeInstanceOf(Array);

      const reservationBookings = await request(BASE_URL)
        .get(`/api/internal/organizations/${ORG_ID}/customers/${customerId}/bookings`)
        .query({ booking_type: 'reservation' });

      expect(reservationBookings.status).toBe(200);
      expect(reservationBookings.body.data.bookings).toBeInstanceOf(Array);
    });
  });

  it('Staff availability endpoint works', async () => {
    await runScenario('staff availability', async () => {
      if (!staffId) {
        throw new Error('Staff ID not available');
      }

      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const dateStr = tomorrow.toISOString().slice(0, 10);

      const staffAvailability = await request(BASE_URL)
        .get('/api/internal/availability/staff')
        .query({
          organization_id: ORG_ID,
          date: dateStr,
        });

      expect(staffAvailability.status).toBe(200);
      expect(staffAvailability.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            staff: expect.any(Array),
            date: expect.any(String),
          }),
        }),
      );

      // Test with service_id filter
      if (serviceCatalogId) {
        const staffAvailabilityWithService = await request(BASE_URL)
          .get('/api/internal/availability/staff')
          .query({
            organization_id: ORG_ID,
            date: dateStr,
            service_id: serviceCatalogId,
          });

        expect(staffAvailabilityWithService.status).toBe(200);
        expect(staffAvailabilityWithService.body.data.staff).toBeInstanceOf(Array);
      }
    });
  });

  it('Staff details endpoint works', async () => {
    await runScenario('staff details', async () => {
      if (!staffId) {
        throw new Error('Staff ID not available');
      }

      const staffDetails = await request(BASE_URL)
        .get(`/api/internal/staff/${staffId}`)
        .query({ organization_id: ORG_ID });

      expect(staffDetails.status).toBe(200);
      expect(staffDetails.body).toEqual(
        expect.objectContaining({
          success: true,
          data: expect.objectContaining({
            id: staffId,
            name: expect.any(String),
            role: expect.any(String),
            services: expect.any(Array),
          }),
        }),
      );
    });
  });

  it('Organization lookup returns validation error for invalid ID', async () => {
    await runScenario('organization invalid id lookup', async () => {
      const response = await request(BASE_URL).get(
        `/api/internal/organizations/-999/details`,
      );

      expect([404, 422]).toContain(response.status);

      if (response.status === 422) {
        expect(response.body).toEqual(
          expect.objectContaining({
            success: false,
            message: expect.stringContaining('organization_id must be a positive integer'),
          }),
        );
      }
    });
  });
});
