import json
import logging
import os

import boto3


LOGGER = logging.getLogger()

QUEUE_URL_ENV_VAR = "PGVECTOR_UPDATE_QUEUE"
BUCKET_ENV_VAR = "BUCKET_NAME"

MALFORMED_EVENT_LOG_MSG = "Malformed event. Skipping this record."


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
    

def _record_validation(record, source_bucket_name):
    """Validate record
    
    :param record: Dictionary
    :param source_bucket_name: String

    :rtype: Boolean
    """
    # validate eventSource
    _validate_field(record, "eventSource", "aws:s3")
    
    # validate eventSource
    _check_missing_field(record, "eventName")
    if not record["eventName"].startswith("ObjectCreated"):
        LOGGER.warning(
            "Found a non ObjectCreated event, ignoring this record")
        return False
        
    # check for 's3' in response elements
    _check_missing_field(record, "s3")
    
    s3_data = record["s3"]
    # validate s3 data
    _check_missing_field(s3_data, "bucket")
    _validate_field(s3_data["bucket"], "name", source_bucket_name)

    # check for object
    _check_missing_field(s3_data, "object")
    # check for key
    _check_missing_field(s3_data["object"], "key")

    return True


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
    
    # check for DESTINATION_BUCKET_NAME env var
    bucket_name = os.environ.get(BUCKET_ENV_VAR)
    if not bucket_name:
        raise MissingEnvironmentVariable(BUCKET_ENV_VAR)
    LOGGER.info(f"destination bucket: {bucket_name}")

    queue_url = os.environ.get(QUEUE_URL_ENV_VAR)
    if not queue_url:
        raise MissingEnvironmentVariable(
            f"{QUEUE_URL_ENV_VAR} environment variable is required")

    # check for 'records' field in the event
    _check_missing_field(event, "Records")
    records = event["Records"]

    if not isinstance(records, list):
        raise Exception("'Records' is not a list")
    LOGGER.info("Extracted 'Records' from the event")
    
    sqs_client = boto3.client("sqs")
    for record in records:
        try:
            valid_record = _record_validation(record, bucket_name)
        except KeyError:
            LOGGER.warning(MALFORMED_EVENT_LOG_MSG)
            continue
        except ValueError:
            LOGGER.warning(MALFORMED_EVENT_LOG_MSG)
            continue
        if not valid_record:
            LOGGER.warning(
                "record could not be validated. Skipping this one.")
            continue
        
        _send_message_to_sqs(
            sqs_client, 
            queue_url, 
            {
                "bucket": bucket_name,
                "file": record["s3"]["object"]["key"]
            }
        )
    sqs_client.close()
