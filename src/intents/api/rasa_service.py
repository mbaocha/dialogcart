"""
Rasa NLU Service - Pure Rasa functionality
"""
import os
import sys
import asyncio
import logging
import shutil
import threading
from pathlib import Path

from filelock import FileLock, Timeout as FileLockTimeout

from rasa.__main__ import main as rasa_main
from rasa.model import get_latest_model
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.training_data.training_data import TrainingData
from rasa.shared.nlu.training_data.loading import load_data
from rasa.shared.nlu.training_data.formats.rasa_yaml import RasaYAMLWriter
from rasa.core.agent import Agent

# Import normalizer from trainings directory
sys.path.insert(0, str(Path(__file__).parent.parent / "trainings"))
import normalization.normalizer  # Ensures it's registered

# Setup logging
logger = logging.getLogger(__name__)

# Set matplotlib env for container compatibility
os.environ["MPLCONFIGDIR"] = "./.matplotlib"
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

# Define paths (container-aware)
# Models are persisted under /app/storage (mounted via docker-compose)
BASE_DIR = Path("/app/storage")
MODEL_DIR = BASE_DIR
NLU_DATA_FILE = BASE_DIR / "nlu_data.yml"

# Trainings directory lives under the intents package alongside this file
_TRAININGS_DIR = Path(__file__).resolve().parent.parent / "trainings"

# Default paths (will be resolved to existing ones below)
INITIAL_TRAINING_DATA = str(_TRAININGS_DIR / "initial_training_data.yml")
CONFIG_FILE = str(_TRAININGS_DIR / "config.yml")

# Resolve to first existing candidate for robustness across run contexts
def _resolve_first_existing(candidates: list[str]) -> str:
    for c in candidates:
        try:
            if os.path.exists(c):
                return c
        except Exception:
            continue
    return candidates[0]

# Candidate locations for trainings files
_init_candidates = [
    str(_TRAININGS_DIR / "initial_training_data.yml"),
    "/app/src/intents/trainings/initial_training_data.yml",
    "/app/src/initial_training_data.yml",
    str((Path.cwd() / "initial_training_data.yml").resolve()),
]
_config_candidates = [
    str(_TRAININGS_DIR / "config.yml"),
    "/app/src/intents/trainings/config.yml",
    "/app/src/config.yml",
    str((Path.cwd() / "config.yml").resolve()),
]

INITIAL_TRAINING_DATA = _resolve_first_existing(_init_candidates)
CONFIG_FILE = _resolve_first_existing(_config_candidates)

MODEL_DIR.mkdir(parents=True, exist_ok=True)
BASE_DIR.mkdir(parents=True, exist_ok=True)

# Cross-process lock file
TRAIN_LOCK_FILE = "/tmp/rasa_train.lock"

# In-process lock
_lock = threading.Lock()
_global_agent = None


class RasaService:
    """Rasa NLU service for training and parsing"""
    
    def __init__(self):
        self.agent = None
        self.initialize()
    
    def initialize(self):
        """Initialize the Rasa service by loading or training a model."""
        logger.info("🚀 Starting Rasa NLU Service...")

        # Debug: log resolved important paths inside the container
        try:
            logger.info(
                "Resolved paths => INITIAL_TRAINING_DATA=%s (exists=%s), CONFIG_FILE=%s (exists=%s), MODEL_DIR=%s",
                INITIAL_TRAINING_DATA,
                os.path.exists(INITIAL_TRAINING_DATA),
                CONFIG_FILE,
                os.path.exists(CONFIG_FILE),
                str(MODEL_DIR),
            )
            trainings_dir = Path(__file__).resolve().parent.parent / "trainings"
            logger.info("Trainings dir listing: %s", list(trainings_dir.iterdir()))
        except Exception:
            # Best-effort diagnostics only
            pass

        if self.load_agent():
            logger.info("✅ Model loaded successfully - Ready for predictions!")
        else:
            logger.info("⚠️ No model found - Attempting to train from defaults...")

            # Cross-process lock so only one worker does init + training
            train_lock = FileLock(TRAIN_LOCK_FILE, timeout=900)  # 15 min max
            try:
                with train_lock:
                    logger.info("🔒 Acquired training lock.")
                    if self.load_agent():
                        logger.info("✅ Model loaded by another process.")
                    elif self.initialize_training_data() and self.train_initial_model():
                        if self.load_agent():
                            logger.info("✅ Initial model trained and loaded.")
                        else:
                            logger.error("❌ Failed to load trained model.")
                    else:
                        logger.error("❌ Failed to train or initialize model.")
            except FileLockTimeout:
                logger.error("⏳ Could not acquire training lock in time; proceeding without training.")
            finally:
                if os.path.exists(TRAIN_LOCK_FILE):
                    logger.info("🔓 Training lock released (or not acquired).")

    def load_agent(self):
        """Try loading the global agent from latest model."""
        global _global_agent
        try:
            model_path = get_latest_model(str(MODEL_DIR))
            if model_path:
                _global_agent = Agent.load(model_path)
                self.agent = _global_agent
                return True
            logger.warning("No model path found.")
        except Exception as e:
            logger.error(f"Failed to load global agent: {e}")
        _global_agent = None
        self.agent = None
        return False

    def reload_agent(self):
        """Reload the global agent after training."""
        return self.load_agent()

    def get_agent(self):
        """Return the loaded agent or try to load one."""
        if self.agent is None:
            self.load_agent()
        return self.agent

    def initialize_training_data(self):
        """Copy initial training data to working directory."""
        # Detailed diagnostics for path resolution
        try:
            logger.info(
                "[TRAINING PATH CHECK] cwd=%s, module=%s, training_path=%s, exists=%s",
                os.getcwd(),
                __file__,
                INITIAL_TRAINING_DATA,
                os.path.exists(INITIAL_TRAINING_DATA),
            )
            import sys as _sys
            logger.info("[TRAINING PATH CHECK] sys.path (head)=%s", _sys.path[:5])
            tp_parent = str(Path(INITIAL_TRAINING_DATA).parent)
            try:
                logger.info("[TRAINING PATH CHECK] parent dir=%s listing=%s", tp_parent, list(Path(tp_parent).iterdir()))
            except Exception:
                pass
        except Exception:
            pass

        if not os.path.exists(INITIAL_TRAINING_DATA):
            logger.warning("Training data not found at: %s (cwd=%s)", INITIAL_TRAINING_DATA, os.getcwd())
            return False

        try:
            shutil.copy2(INITIAL_TRAINING_DATA, NLU_DATA_FILE)
            logger.info(f"Initialized training data from {INITIAL_TRAINING_DATA}")
            return True
        except Exception as e:
            logger.error(f"Failed to copy training data: {e}")
            return False

    def train_initial_model(self):
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
                    result = self.train_model_with_lock()
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

    def build_nlu_data(self, intent, examples):
        """Construct NLU training examples for a given intent."""
        messages = [Message.build(text=e, intent=intent) for e in examples]
        return TrainingData(training_examples=messages)

    def append_to_training_file(self, intent, examples):
        """Append new intent examples to existing training file."""
        if NLU_DATA_FILE.exists():
            data = load_data(str(NLU_DATA_FILE))
            data.training_examples += self.build_nlu_data(intent, examples).training_examples
        else:
            data = self.build_nlu_data(intent, examples)

        with _lock:
            writer = RasaYAMLWriter()
            writer.dump(str(NLU_DATA_FILE), data)

    def train_model(self):
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

    def train_model_with_lock(self):
        """Train a new NLU model with in-process lock protection (thread-safe)."""
        with _lock:
            return self.train_model()

    async def parse_text_async(self, text):
        """Parse user text using the agent (async)."""
        agent = self.get_agent()
        if not agent:
            raise RuntimeError("No model available")
        return await agent.parse_message(text)

    def parse_text_sync(self, text):
        """Run async parse_text in sync context (for Flask)."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(self.parse_text_async(text))
        finally:
            asyncio.set_event_loop(None)

    def train_intent(self, intent, examples):
        """Train a new intent with examples."""
        try:
            self.append_to_training_file(intent, examples)

            # Ensure multiple intents exist
            if NLU_DATA_FILE.exists():
                data = load_data(str(NLU_DATA_FILE))
                unique_intents = set(ex.get("intent") for ex in data.training_examples if ex.get("intent"))
                if len(unique_intents) <= 1:
                    self.append_to_training_file("goodbye", ["bye", "goodbye", "see you later"])

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
                        result = self.train_model_with_lock()
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
                self.reload_agent()
                return {"ok": True, "model_path": model_path}
            else:
                logger.error("Training timed out after 5 minutes")
                return {"error": "Training timed out after 5 minutes"}

        except Exception as e:
            logger.exception("Training failed.")
            return {"error": f"Training failed: {str(e)}"}

    def predict(self, text, sender_id="anonymous"):
        """Predict intent and entities for given text."""
        try:
            # Parse with local NLU model
            nlu = self.parse_text_sync(text)
            
            # Get entities and intent from Rasa result
            entities = nlu.get("entities") or []
            intent = nlu.get("intent", {}).get("name") if nlu.get("intent") else None
            
            return {
                "nlu": nlu,
                "intent": intent,
                "entities": entities,
                "slots": {},
                "sender_id": sender_id
            }
        except Exception as e:
            logger.exception("Prediction failed.")
            return {"error": f"Prediction error: {str(e)}"}
