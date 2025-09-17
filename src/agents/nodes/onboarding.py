"""
Onboarding node for user registration and profile management.
"""

import re
from typing import Optional

from langchain_core.messages import AIMessage

from agents.state import AgentState
from agents.utils import enforce_agent_state
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from utils.coreutil import split_name
from features.customer import update_customer, _default_tenant_id


def extract_name_email(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract name and email from text."""
    text = text.strip()
    
    # Find email
    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    email = email_match.group() if email_match else None
    
    # Extract name
    if email:
        name_part = text.replace(email, '').strip().rstrip(',').strip()
    else:
        name_part = text.split(',')[0].strip() if ',' in text else text
    
    # Basic validation
    name = name_part.title() if name_part and re.match(r'^[a-zA-Z\s]+$', name_part) else None
    
    return name, email


def validate_email(email: str) -> bool:
    """Basic email validation."""
    if not email:
        return False
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email))


SAMPLE_NAME_EMAIL = "Chinedu Olamide, chinedu@example.com"


def process_onboarding(message: str) -> dict:
    """Process onboarding message and return response."""
    name, email = extract_name_email(message)

    # Error cases
    if not name and not email:
        return {
            "response": (
                "Hmm... I didn't catch your name or email. Could you please send both?\n"
                f"📝 Example: *{SAMPLE_NAME_EMAIL}*"
            )
        }

    if not name:
        return {
            "response": (
                "I couldn't find your name. Could you include your full name?\n"
                f"📝 Example: *{SAMPLE_NAME_EMAIL}*"
            )
        }

    if not email:
        return {
            "response": (
                "Oops! I didn't see an email in your message. Please resend it with your full name.\n"
                "📝 Like this: *Chinedu Olamide, chinedu@example.com*"
            )
        }

    if not validate_email(email):
        return {
            "response": (
                f"'{email}' doesn't seem to be a valid email. Could you check it and try again?\n"
                f"📝 Example: *{SAMPLE_NAME_EMAIL}*"
            )
        }

    # Success
    return {
        "name": name,
        "email": email,
        "response": (
            f"Thanks, {name} 😊\n"
            f"Just to confirm — is this your correct name and email: **{name}, {email}**?\n"
            "✅ Reply *Yes* to continue\n"
            f"✏️ Or send the correct one in a similar format: *{SAMPLE_NAME_EMAIL}*"
        )
    }


@enforce_agent_state
def onboarding_node(state: AgentState) -> AgentState:
    """Handle user onboarding and registration."""
    print(f"[DEBUG] onboarding_node received type: {type(state)}")
    user_input = state.user_input.lower().strip()
    user_profile = state.user_profile
    onboarding_result = process_onboarding(user_input)
    new_state = state

    if (user_profile.get("name") or user_profile.get("first_name")) and user_profile.get("email"):
        if user_input == "yes":
            full_name = user_profile.get("name", "")
            first_name, last_name = split_name(full_name)
            updated_user_profile = {
                **user_profile,
                "first_name": first_name,
                "last_name": last_name
            }
            
            # Step 1: Build state with updated profile, registration flags
            candidate_state = state.model_copy(update={
                "is_registered": True,
                "just_registered": True,
                "user_profile": updated_user_profile,
                "user_input": ""
            })
            agent_state_dict = candidate_state.model_dump()
            # Ensure required identifiers are present for update_customer
            tenant_id = agent_state_dict.get("tenant_id") or _default_tenant_id()
            customer_id = agent_state_dict.get("customer_id") or getattr(candidate_state, "customer_id", None)
            user_result = update_customer(agent_state_dict={
                **agent_state_dict,
                "tenant_id": tenant_id,
                "customer_id": customer_id,
            })

            # Step 2: Decide next state/message
            if user_result.get("success"):
                print(f"[DEBUG] Customer registered successfully: {user_result['data'].get('customer_id')}")
                new_state = candidate_state.model_copy(update={
                    "messages": [AIMessage(content="🎉 Registration complete! Welcome to Bulkpot!")]
                })
            else:
                print(f"[DEBUG] User registration failed: {user_result.get('error')}")
                new_state = state.model_copy(update={
                    "messages": [AIMessage(content="❌ Sorry, there was a problem saving your registration. Please try again.")],
                    "is_registered": False,
                    "just_registered": False,
                    "user_input": ""
                })

        elif user_input == "no":
            new_state = state.model_copy(update={
                "messages": [AIMessage(content="No problem! Please provide your name and email again.\n📝 Format: Name, email@domain.com")],
                "user_profile": {},
                "is_registered": False,
                "just_registered": False
            })
    elif onboarding_result.get("name") and onboarding_result.get("email"):
        user_profile = {
            "name": onboarding_result["name"],
            "email": onboarding_result["email"]
        }
        new_state = state.model_copy(update={
            "messages": [AIMessage(content=onboarding_result["response"])],
            "user_profile": user_profile,
            "is_registered": False,
            "user_input": ""
        })
    elif user_profile.get("first_name") and user_profile.get("email"):
        # User already has complete profile but hasn't confirmed
        new_state = state.model_copy(update={
            "messages": [AIMessage(content="I see you already have a profile! Just to confirm — is this your correct information?\n\n**Name:** " + user_profile.get("first_name", "") + " " + user_profile.get("last_name", "") + "\n**Email:** " + user_profile.get("email", "") + "\n\n✅ Reply *Yes* to continue\n✏️ Or send the correct information")],
            "is_registered": False,
            "user_input": ""
        })
    else:
        new_state = state.model_copy(update={
            "messages": [AIMessage(content=onboarding_result["response"])],
            "user_input": ""
        })
    print(f"[DEBUG] onboarding_node returns type: {type(new_state)}")
    return new_state 