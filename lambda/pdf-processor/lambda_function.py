from io import BytesIO
import logging
import os

import boto3
from pypdf import PdfReader
from pypdf.errors import PdfReadError


LOGGER = logging.getLogger(__name__)

SOURCE_BUCKET_ENV_VAR = "SOURCE_BUCKET_NAME"
DEST_BUCKET_ENV_VAR = "DESTINATION_BUCKET_NAME"

MALFORMED_EVENT_LOG_MSG = "Malformed event. Skipping this record."


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


def _silence_noisy_loggers():
    """Silence chatty libraries for better logging"""
    for logger in ['boto3', 'botocore',
                   'botocore.vendored.requests.packages.urllib3']:
        logging.getLogger(logger).setLevel(logging.WARNING)


def _configure_logger():
    """Configure python logger for lambda function"""
    default_log_args = {
        "level": logging.DEBUG if os.environ.get("VERBOSE", False) else logging.INFO,
        "format": "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        "datefmt": "%d-%b-%y %H:%M",
        "force": True,
    }
    logging.basicConfig(**default_log_args)


def _check_missing_field(validation_dict, extraction_key):
    """Check if a field exists in a dictionary

    :param validation_dict: Dictionary
    :param extraction_key: String

    :raises: KeyError
    """
    extracted_value = validation_dict.get(extraction_key)
    
    if not extracted_value:
        LOGGER.error(f"Missing '{extraction_key}' key in the dict")
        raise KeyError
    

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
        LOGGER.warning("Found a non ObjectCreated event, ignoring this record")
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


def _get_source_file_contents(client, bucket, filename):
    """Fetch the contents of the file

    :param client: boto3 Client Object (S3)
    :param bucket: String
    :param filename: String

    :rtype Bytes
    """
    resp = client.get_object(Bucket=bucket, Key=filename)

    _check_missing_field(resp, "ResponseMetadata")

    _validate_field(resp["ResponseMetadata"], "HTTPStatusCode", 200)

    _check_missing_field(resp, "Body")
    
    return resp["Body"].read()


def lambda_handler(event, context):
    """What executes when the program is run"""

    # configure python logger for Lambda
    _configure_logger()
    # silence chatty libraries for better logging
    _silence_noisy_loggers()

    # check for SOURCE_BUCKET_NAME env var
    source_bucket_name = os.environ.get(SOURCE_BUCKET_ENV_VAR)
    if not source_bucket_name:
        raise MissingEnvironmentVariable(SOURCE_BUCKET_ENV_VAR)
    LOGGER.info(f"source bucket: {source_bucket_name}")
    
    # check for DESTINATION_BUCKET_NAME env var
    dest_bucket_name = os.environ.get(DEST_BUCKET_ENV_VAR)
    if not dest_bucket_name:
        raise MissingEnvironmentVariable(DEST_BUCKET_ENV_VAR)
    LOGGER.info(f"destination bucket: {dest_bucket_name}")

    # check for 'records' field in the event
    _check_missing_field(event, "Records")
    records = event["Records"]

    if not isinstance(records, list):
        raise Exception("'Records' is not a list")
    LOGGER.info("Extracted 'Records' from the event")
    
    for record in records:
        try:
            valid_record = _record_validation(record, source_bucket_name)
        except KeyError:
            LOGGER.warning(MALFORMED_EVENT_LOG_MSG)
            continue
        except ValueError:
            LOGGER.warning(MALFORMED_EVENT_LOG_MSG)
            continue
        
        if not valid_record:
            LOGGER.warning("record could not be validated. Skipping this one.")
            continue
        file_name = record["s3"]["object"]["key"]
        LOGGER.info(
            f"Valid record found. Will attempt to process the file: {file_name}")
        
        s3_client = boto3.client("s3")
        file_contents = _get_source_file_contents(
            s3_client, source_bucket_name, file_name)
        
        try:
            pdf = PdfReader(BytesIO(file_contents))
        except PdfReadError as err:
            LOGGER.error(err)
            LOGGER.warning(
                f"{file_name} is invalid and/or corrupt, skipping.")
            continue 

        LOGGER.info("Extracting text from pdf..")
        text_file_contents = ""
        for page in pdf.pages:
            text_file_contents = f"{text_file_contents}\n{page.extract_text()}"

        s3_resource = boto3.resource("s3")
        LOGGER.debug("Writing file to S3")
        s3_resource.Bucket(dest_bucket_name).put_object(
            Key=file_name.replace(".pdf", ".txt"), 
            Body=text_file_contents.encode("utf-8"))
        LOGGER.info("Successfully converted pdf to txt, and uploaded to s3")

    LOGGER.debug("Closing s3 boto3 client")
    s3_client.close()
