import logging
import os

import toml

import helper_functions as hfn


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""

DB_SECRET_ENV_VAR = "DB_CREDS"
API_KEY_SECRET_ENV_VAR = "API_KEY_SECRET_NAME"

DEFAULT_LOG_LEVEL = logging.INFO
LOGGER = logging.getLogger(__name__)
LOGGING_FORMAT = "%(asctime)s %(levelname)-5.5s " \
                 "[%(name)s]:[%(threadName)s] " \
                 "%(message)s"


if __name__ == "__main__":
  
  streamlit_secrets = {}

  # logging configuration
  log_level = DEFAULT_LOG_LEVEL
  if os.environ.get("VERBOSE", "").lower() == "true":
     log_level = logging.DEBUG
  logging.basicConfig(level=log_level, format=LOGGING_FORMAT)

  # vector db secret fetch
  secret_name = os.environ.get(DB_SECRET_ENV_VAR)
  if not secret_name:
     raise MissingEnvironmentVariable(f"{DB_SECRET_ENV_VAR} environment variable is required")
  streamlit_secrets.update(hfn.get_secret_from_name(secret_name))
  
  # open ai api key fetch
  openai_secret = os.environ.get(API_KEY_SECRET_ENV_VAR)
  if not openai_secret:
     raise MissingEnvironmentVariable(f"{API_KEY_SECRET_ENV_VAR} environment variable is required")
  streamlit_secrets["OPENAI_API_KEY"] = hfn.get_secret_from_name(openai_secret, kv=False)

  LOGGER.info("Writing streamlit secrets")
  with open("/root/.streamlit/secrets.toml", "w") as file:
     toml.dump(streamlit_secrets, file)
  