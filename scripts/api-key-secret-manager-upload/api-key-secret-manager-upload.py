import argparse
import getpass
import logging
import time

import boto3


DEFAULT_LOG_LEVEL = logging.INFO
LOGGER = logging.getLogger(__name__)
LOGGING_FORMAT = "%(asctime)s %(levelname)-5.5s " \
                 "[%(name)s]:[%(threadName)s] " \
                 "%(message)s"


def _check_missing_field(validation_dict, extraction_key):
    """Check if a field exists in a dictionary

    :param validation_dict: Dictionary
    :param extraction_key: String

    :raises: Exception
    """
    extracted_value = validation_dict.get(extraction_key)
    
    if not extracted_value:
        LOGGER.error(f"Missing '{extraction_key}' key in the dict")
        raise Exception
    

def _validate_field(validation_dict, extraction_key, expected_value):
    """Validate the passed in field

    :param validation_dict: Dictionary
    :param extraction_key: String
    :param expected_value: String

    :raises: ValueError
    """
    extracted_value = validation_dict.get(extraction_key)
    _check_missing_field(validation_dict, extraction_key)
    
    if extracted_value != expected_value:
        LOGGER.error(f"Incorrect value found for '{extraction_key}' key")
        raise ValueError


def _cli_args():
    """Parse CLI Args
    
    :rtype: argparse.Namespace
    """
    parser = argparse.ArgumentParser(description="api-key-secret-manager-upload")

    parser.add_argument("-s",
                        "--secret-name",
                        type=str,
                        help="Secret Name",
                        required=True
                        )
    parser.add_argument("-p",
                        "--aws-profile",
                        type=str,
                        default="default",
                        help="AWS profile to be used for the API calls")
    parser.add_argument("-v",
                        "--verbose",
                        action="store_true",
                        help="debug log output")
    return parser.parse_args()


def _silence_noisy_loggers():
    """Silence chatty libraries for better logging"""
    for logger in ['boto3', 'botocore',
                   'botocore.vendored.requests.packages.urllib3']:
        logging.getLogger(logger).setLevel(logging.WARNING)


def main():
    """What executes when the script is run"""
    start = time.time() # to capture elapsed time

    args = _cli_args()

    # logging configuration
    log_level = DEFAULT_LOG_LEVEL
    if args.verbose:
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level, format=LOGGING_FORMAT)
    # silence chatty libraries
    _silence_noisy_loggers()

    LOGGER.info(f"AWS Profile being used: {args.aws_profile}")
    boto3.setup_default_session(profile_name=args.aws_profile)

    sm_client = boto3.client("secretsmanager")

    LOGGER.info(f"Updating Secret: {args.secret_name}")

    resp = sm_client.update_secret(
        SecretId=args.secret_name,
        SecretString=getpass.getpass("Please enter the API Key: ")
    )
    _check_missing_field(resp, "ResponseMetadata")
    _validate_field(resp["ResponseMetadata"], "HTTPStatusCode", 200)
    LOGGER.info("Successfully updated secret value")

    LOGGER.debug("Closing secretsmanager boto3 client")
    sm_client.close()

    LOGGER.info(f"Total time elapsed: {time.time() - start} seconds")


if __name__ == "__main__":
    main()
