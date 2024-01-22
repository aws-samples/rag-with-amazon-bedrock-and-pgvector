import json
import logging
import os

import boto3


LOGGER = logging.getLogger()

RDS_DDL_QUEUE_URL_ENV_VAR = "RDS_DDL_QUEUE_URL"


class MalformedEvent(Exception):
    """Raised if a malformed event received"""
    

class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


def _silence_noisy_loggers():
    """Silence chatty libraries for better logging"""
    for logger in ['boto3', 'botocore',
                   'botocore.vendored.requests.packages.urllib3']:
        logging.getLogger(logger).setLevel(logging.WARNING)


def _configure_logger():
    """Configure python logger"""
    level = logging.INFO
    verbose = os.environ.get("VERBOSE", "")
    if verbose.lower() == "true":
        print("Will set the logging output to DEBUG")
        level = logging.DEBUG
    
    if len(logging.getLogger().handlers) > 0:
        # The Lambda environment pre-configures a handler logging to stderr. 
        # If a handler is already configured, `.basicConfig` does not execute. 
        # Thus we set the level directly.
        logging.getLogger().setLevel(level)
    else:
        logging.basicConfig(level=level)


def _check_missing_field(validation_dict, extraction_key):
    """Check if a field exists in a dictionary

    :param validation_dict: Dictionary
    :param extraction_key: String

    :raises: MalformedEvent
    """
    extracted_value = validation_dict.get(extraction_key)
    
    if not extracted_value:
        LOGGER.error(f"Missing '{extraction_key}' field in the event")
        raise MalformedEvent
    

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
        LOGGER.error(f"Incorrect value found for '{extraction_key}' field")
        raise ValueError
    

def _extract_valid_event(event):
    """Validate incoming event and extract necessary attributes
    
    :param event: Dictionary
    
    :raises: MalformedEvent
    :raises: ValueError
    
    :rtype: Dictionary
    """
    valid_event = {}

    _validate_field(event, "source", "aws.rds")
    
    _check_missing_field(event, "detail")
    event_detail = event["detail"]
    
    _validate_field(event_detail, "eventName", "CreateDBInstance")

    _check_missing_field(event_detail, "responseElements")
    
    return event_detail["responseElements"]


def _send_message_to_sqs(client, queue_url, message_dict):
    """Send message to SQS Queue

    :param client: Boto3 client object (SQS)
    :param queue_url: String
    :param message_dict: Dictionary

    :raises: Exception
    """
    LOGGER.info(f"Attempting to send message to: {queue_url}")
    resp = client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message_dict)
    )

    _check_missing_field(resp, "ResponseMetadata")
    resp_metadata = resp["ResponseMetadata"]

    _check_missing_field(resp_metadata, "HTTPStatusCode")
    status_code = resp_metadata["HTTPStatusCode"]
    
    if status_code == 200:
        LOGGER.info("Successfully pushed message")
    else:
        raise Exception("Unable to push message")   

   
def lambda_handler(event, context):
    """What executes when the program is run"""
    
    # configure python logger
    _configure_logger()
    # silence chatty libraries
    _silence_noisy_loggers()
    
    valid_event = _extract_valid_event(event)
    LOGGER.info("Extracted data to send to SQS")

    sqs_client = boto3.client("sqs")
    rds_ddl_queue_url = os.environ.get(RDS_DDL_QUEUE_URL_ENV_VAR)
    if not rds_ddl_queue_url:
        raise MissingEnvironmentVariable(
            f"{RDS_DDL_QUEUE_URL_ENV_VAR} environment variable is required")
    
    # send message to DDL Triggering Queue
    _send_message_to_sqs(
        sqs_client, 
        rds_ddl_queue_url, 
        valid_event)

    sqs_client.close()
