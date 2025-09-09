import os
import sys
import asyncio
import logging
import shutil
import threading
from pathlib import Path
from flask import Flask, request, jsonify

from filelock import FileLock, Timeout as FileLockTimeout  # NEW

from rasa.__main__ import main as rasa_main
from rasa.model import get_latest_model
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.training_data.training_data import TrainingData
from rasa.shared.nlu.training_data.loading import load_data
from rasa.shared.nlu.training_data.formats.rasa_yaml import RasaYAMLWriter
from rasa.core.agent import Agent

import normalization.normalizer  # 👈 Ensures it's registered
print("✅ normalization.normalizer imported from app.py")

from shared.conversation_manager import SharedConversationManager
from shared.session_client import SessionClient


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set matplotlib env for container compatibility
os.environ["MPLCONFIGDIR"] = "./.matplotlib"
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

# Define paths
BASE_DIR = Path("./model_storage")
MODEL_DIR = BASE_DIR / "models"
NLU_DATA_FILE = BASE_DIR / "nlu_data.yml"
INITIAL_TRAINING_DATA = "initial_training_data.yml"
CONFIG_FILE = "rasa_config.yml"

MODEL_DIR.mkdir(parents=True, exist_ok=True)
BASE_DIR.mkdir(parents=True, exist_ok=True)

# Cross-process lock file (prevents multiple gunicorn workers from training at once)
TRAIN_LOCK_FILE = "/tmp/rasa_train.lock"  # NEW

# In-process lock
_lock = threading.Lock()
app = Flask(__name__)
_global_agent = None

# ─────────────────────────────────────────────────────────────
# Session and conversation management
# ─────────────────────────────────────────────────────────────
def get_conversation_manager(sender_id: str) -> SharedConversationManager:
    """Get a conversation manager instance for the given sender."""
    session_client = SessionClient('http://session:9200')
    return SharedConversationManager(session_client, sender_id)

# ─────────────────────────────────────────────────────────────


def initialize_app():
    """Initialize the app by loading or training a model."""
    logger.info("🚀 Starting Rasa NLU Service...")

    if load_global_agent():
        logger.info("✅ Model loaded successfully - Ready for predictions!")
    else:
        logger.info("⚠️ No model found - Attempting to train from defaults...")

        # Cross-process lock so only one worker does init + training
        train_lock = FileLock(TRAIN_LOCK_FILE, timeout=900)  # 15 min max  # NEW
        try:
            with train_lock:
                logger.info("🔒 Acquired training lock.")
                if load_global_agent():
                    logger.info("✅ Model loaded by another process.")
                elif initialize_training_data() and train_initial_model():
                    if load_global_agent():
                        logger.info("✅ Initial model trained and loaded.")
                    else:
                        logger.error("❌ Failed to load trained model.")
                else:
                    logger.error("❌ Failed to train or initialize model.")
        except FileLockTimeout:
            logger.error("⏳ Could not acquire training lock in time; proceeding without training.")
        finally:
            if os.path.exists(TRAIN_LOCK_FILE):
                # Leave the lock file; FileLock handles it. No manual cleanup required.
                logger.info("🔓 Training lock released (or not acquired).")

    logger.info("🌐 Server running on http://0.0.0.0:8000")
    logger.info("📡 Awaiting requests...")
    logger.info("-" * 50)


def load_global_agent():
    """Try loading the global agent from latest model."""
    global _global_agent
    try:
        model_path = get_latest_model(str(MODEL_DIR))
        if model_path:
            _global_agent = Agent.load(model_path)
            return True
        logger.warning("No model path found.")
    except Exception as e:
        logger.error(f"Failed to load global agent: {e}")
    _global_agent = None
    return False


def reload_global_agent():
    """Reload the global agent after training."""
    return load_global_agent()


def get_global_agent():
    """Return the loaded agent or try to load one."""
    global _global_agent
    if _global_agent is None:
        load_global_agent()
    return _global_agent


def initialize_training_data():
    """Copy initial training data to working directory."""
    if not os.path.exists(INITIAL_TRAINING_DATA):
        logger.warning(f"{INITIAL_TRAINING_DATA} not found.")
        return False

    try:
        shutil.copy2(INITIAL_TRAINING_DATA, NLU_DATA_FILE)
        logger.info(f"Initialized training data from {INITIAL_TRAINING_DATA}")
        return True
    except Exception as e:
        logger.error(f"Failed to copy training data: {e}")
        return False


def train_initial_model():
    """Train an initial model if no model exists."""
    try:
        logger.info("🧠 Starting initial model training...")

        # Clear cache directory (ignore errors)
        cache_dir = ".rasa/cache"
        try:
            shutil.rmtree(cache_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Cache cleanup warning (ignored): {e}")

        # Run training in a background thread with logging
        training_completed = threading.Event()
        training_result = [None]
        training_exception = [None]

        def training_worker():
            logger.info("🔄 Training thread started...")
            try:
                result = train_model_with_lock()
                training_result[0] = result
                logger.info("✅ Training thread finished successfully.")
            except Exception as e:
                training_exception[0] = e
                logger.exception("💥 Training thread crashed.")
            finally:
                training_completed.set()

        t = threading.Thread(target=training_worker, name="rasa-train-thread", daemon=True)
        t.start()

        logger.info("⏳ Waiting up to 10 minutes for model training to complete...")
        if training_completed.wait(timeout=600):
            if training_exception[0]:
                raise training_exception[0]
            model_path = training_result[0]
            logger.info(f"✅ Initial model trained: {model_path}")
            return True
        else:
            logger.error("⏱️ Training timed out after 10 minutes.")
            return False

    except Exception as e:
        logger.error(f"Failed to train initial model: {e}")
        return False


def build_nlu_data(intent, examples):
    """Construct NLU training examples for a given intent."""
    messages = [Message.build(text=e, intent=intent) for e in examples]
    return TrainingData(training_examples=messages)


def append_to_training_file(intent, examples):
    """Append new intent examples to existing training file."""
    if NLU_DATA_FILE.exists():
        data = load_data(str(NLU_DATA_FILE))
        data.training_examples += build_nlu_data(intent, examples).training_examples
    else:
        data = build_nlu_data(intent, examples)

    with _lock:
        writer = RasaYAMLWriter()
        writer.dump(str(NLU_DATA_FILE), data)


def train_model():
    """Train a new NLU model from current training file (via CLI entrypoint)."""
    if not NLU_DATA_FILE.exists():
        raise FileNotFoundError(f"{NLU_DATA_FILE} not found")

    # Ensure cache cleanup won't crash if another process is poking it
    cache_dir = ".rasa/cache"
    try:
        shutil.rmtree(cache_dir, ignore_errors=True)
    except Exception:
        pass

    args = [
        "train", "nlu",
        "--config", CONFIG_FILE,
        "--nlu", str(NLU_DATA_FILE),
        "--out", str(MODEL_DIR),
    ]

    original_argv = sys.argv.copy()
    sys.argv = ["rasa"] + args

    logger.info("🛠️ Invoking rasa CLI to train NLU model...")
    try:
        rasa_main()
        model_path = get_latest_model(str(MODEL_DIR))
        logger.info(f"📦 Model artifact: {model_path}")
        return model_path
    finally:
        sys.argv = original_argv


def train_model_with_lock():
    """Train a new NLU model with in-process lock protection (thread-safe)."""
    with _lock:
        return train_model()


async def parse_text_async(text):
    """Parse user text using the agent (async)."""
    agent = get_global_agent()
    if not agent:
        raise RuntimeError("No model available")
    return await agent.parse_message(text)


def parse_text_sync(text):
    """Run async parse_text in sync context (for Flask)."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(parse_text_async(text))
    finally:
        asyncio.set_event_loop(None)



@app.route("/", methods=["POST"])
def rasa_api():
    """Main Rasa API entrypoint."""
    body = request.get_json(force=True)
    action = body.get("action")

    if action == "train":
        intent = body.get("intent")
        examples = body.get("examples", [])

        if not intent or not examples:
            return _resp(400, {"error": "Both 'intent' and 'examples' are required."})

        try:
            append_to_training_file(intent, examples)

            # Ensure multiple intents exist
            if NLU_DATA_FILE.exists():
                data = load_data(str(NLU_DATA_FILE))
                unique_intents = set(ex.get("intent") for ex in data.training_examples if ex.get("intent"))
                if len(unique_intents) <= 1:
                    append_to_training_file("goodbye", ["bye", "goodbye", "see you later"])

            # Cross-process lock to serialize training from API calls too
            train_lock = FileLock(TRAIN_LOCK_FILE, timeout=900)
            training_completed = threading.Event()
            training_result = [None]
            training_exception = [None]

            def training_worker():
                logger.info("📦 Background training thread started...")
                try:
                    with train_lock:
                        logger.info("🔒 Acquired training lock (API).")
                        result = train_model_with_lock()
                        training_result[0] = result
                        logger.info("📦 Background training completed.")
                except FileLockTimeout:
                    logger.error("⏳ Could not acquire training lock (API) in time.")
                    training_exception[0] = RuntimeError("Training lock timeout")
                except Exception as e:
                    logger.exception("❌ Training failed.")
                    training_exception[0] = e
                finally:
                    training_completed.set()

            t = threading.Thread(target=training_worker, name="rasa-train-api-thread", daemon=True)
            t.start()

            if training_completed.wait(timeout=300):
                if training_exception[0]:
                    raise training_exception[0]
                model_path = training_result[0]
                reload_global_agent()
                return _resp(200, {"ok": True, "model_path": model_path})
            else:
                logger.error("Training timed out after 5 minutes")
                return _resp(500, {"error": "Training timed out after 5 minutes"})

        except Exception as e:
            logger.exception("Training failed.")
            return _resp(500, {"error": f"Training failed: {str(e)}"})

    elif action == "predict":
        text = body.get("text", "").strip()
        sender_id = body.get("sender_id", "anonymous")

        if not text:
            return _resp(400, {"error": "'text' is required."})

        try:
            # Parse with NLU model
            result = parse_text_sync(text)
            
            # Get entities and intent from Rasa result
            entities = result.get("entities") or []
            intent = result.get("intent", {}).get("name") if result.get("intent") else None
            
            # Check if it's a modify_cart and use action-based processing (back-compat: SHOPPING_COMMAND)
            if intent and intent.upper() in ("MODIFY_CART", "SHOPPING_COMMAND"):
                logger.info(f"DEBUG: Rasa app - intent={intent}")
                logger.info(f"DEBUG: Rasa app - entities={entities}")
                
                # Import here to avoid circular imports
                try:
                    from shared.mappers import map_rasa_to_actions
                    from shared.conversation_manager import SharedConversationManager
                    from shared.session_client import SessionClient
                    from shared.models import Action
                except ImportError as e:
                    logger.error(f"Failed to import shared modules: {e}")
                    # Even on import failure, return action-based format for modify_cart
                    return _resp(200, {
                        "nlu": result,
                        "intent": "modify_cart",
                        "actions": [{
                            "action": "unknown",
                            "product": None,
                            "quantity": None,
                            "unit": None,
                            "confidence": "low",
                            "confidence_score": 0.0
                        }],
                        "slots": {},
                        "sender_id": sender_id
                    })
                
                # Parse actions from entities
                actions = map_rasa_to_actions({"nlu": result})
                logger.info(f"DEBUG: Rasa app - parsed actions={actions}")
                
                # Process actions with conversation manager
                session_client = SessionClient()
                conv_mgr = SharedConversationManager(session_client, sender_id)
                processed = conv_mgr.process_actions(text, actions)
                
                # Ensure we always return action-based format for modify_cart
                if not actions:
                    # If no actions were parsed, create a fallback action
                    # This handles cases where verb extraction failed
                    fallback_action = Action(
                        action="set",  # Default action for follow-up commands
                        product=None,  # Will be inherited from slots
                        quantity=None,  # Will be extracted from entities
                        unit=None,
                        confidence=confidence,
                        confidence_score=conf_score
                    )
                    actions = [fallback_action]
                    processed = conv_mgr.process_actions(text, actions)
                
                # Return action-based result
                return _resp(200, {
                    "nlu": result,
                    "intent": "modify_cart",
                    "actions": [action.dict() for action in processed.get("actions", [])],
                    "slots": processed.get("slots"),
                    "sender_id": sender_id
                })
            else:
                # Use traditional intent-based processing for other intents
                conv_mgr = get_conversation_manager(sender_id)
                processed = conv_mgr.process_message(text, entities, intent)
                
                # Return enhanced result with conversation manager processing
                return _resp(200, {
                    "nlu": result,
                    "intent": processed.get("intent"),
                    "entities": processed.get("entities"),
                    "slots": processed.get("slots"),
                    "sender_id": sender_id
                })
        except Exception as e:
            logger.exception("Prediction failed.")
            return _resp(500, {"error": f"Prediction error: {str(e)}"})

    return _resp(404, {"error": f"Unknown action '{action}'."})


def _resp(code, body):
    return jsonify(body), code


# Initialize the app after all functions are defined
initialize_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)