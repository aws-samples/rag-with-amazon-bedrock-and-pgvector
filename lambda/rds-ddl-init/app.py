import json
import logging
import os

import boto3
from botocore.exceptions import ClientError
import queries


DB_NAME_ENV_VAR = "DB_NAME"
SQS_QUEUE_ENV_VAR = "SQS_QUEUE_URL"
DDL_SOURCE_BUCKET_ENV_VAR = "DDL_SOURCE_BUCKET"

DB_IDENTIFIER_KEY = "dBInstanceIdentifier"

LOGGER = logging.getLogger()

DDL_FILE = "rds-ddl.sql"


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
        LOGGER.error(f"Missing '{extraction_key}' key in the dict")
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
        LOGGER.error(f"Incorrect value found for '{extraction_key}' key")
        raise ValueError


def _get_message_body(event):
    """Extract message body from the event
    
    :param event: Dictionary
    
    :raises: MalformedEvent
    
    :rtype: Dictionary
    """
    body = ""
    test_event = event.get("test_event", "")
    if test_event.lower() == "true":
        LOGGER.info("processing test event (and not from SQS)")
        LOGGER.debug("Test body: %s", event)
        return event
    else:
        LOGGER.info("Attempting to extract message body from SQS")
        
        _check_missing_field(event, "Records")
        records = event["Records"]
        
        first_record = records[0]
        
        try:
            body = first_record.get("body")
        except AttributeError:
            raise MalformedEvent("First record is not a proper dict")
        
        if not body:
            raise MalformedEvent("Missing 'body' in the record")
            
        try:
            return json.loads(body)
        except json.decoder.JSONDecodeError:
            raise MalformedEvent("'body' is not valid JSON")
            

def _get_sqs_message_attributes(event):
    """Extract receiptHandle from message
    
    :param event: Dictionary
    
    :raises: MalformedEvent
    
    :rtype: Dictionary
    """
    LOGGER.info("Attempting to extract receiptHandle from SQS")
    records = event.get("Records")
    if not records:
        LOGGER.warning("No receiptHandle found, probably not an SQS message")
        return
    try:
        first_record = records[0]
    except IndexError:
        raise MalformedEvent("Records seem to be empty")
    
    _check_missing_field(first_record, "receiptHandle")
    receipt_handle = first_record["receiptHandle"]
    
    _check_missing_field(first_record, "messageId")
    message_id = first_record["messageId"]
    
    return {
        "message_id": message_id,
        "receipt_handle": receipt_handle
    }


def get_db_secret_from_secret_name(secret_name):
    """Return DB secret from secret name

    :param secret_name: String
    
    :raises: botocore.exceptions.ClientError
    
    :rtype: Dictionary
    """
    session = boto3.session.Session()

    # Initializing Secret Manager's client    
    client = session.client(
        service_name='secretsmanager',
            region_name=os.environ.get("AWS_REGION", session.region_name)
        )
    LOGGER.info(f"Attempting to get secret value for: {secret_name}")
    try:
        get_secret_value_response = client.get_secret_value(
                SecretId=secret_name)
    except ClientError as e:
        # For a list of exceptions thrown, see
        # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
        LOGGER.error("Unable to fetch details from Secrets Manager")
        raise e
    
    _check_missing_field(
        get_secret_value_response, "SecretString")
    try:
        return json.loads(
            get_secret_value_response["SecretString"])
    except json.decoder.JSONDecodeError:
        LOGGER.warning("Secret value is not a valid dictionary")
        return {}


def _fetch_secret_for_db(db_identifier):
    """Fetch the secret arn, name for the database

    :param db_identifier: String

    :rtype: Dictionary
    """
    ret_dict = None
    sm_client = boto3.client("secretsmanager")

    resp = sm_client.list_secrets()

    _check_missing_field(resp, "ResponseMetadata")

    _validate_field(resp["ResponseMetadata"], "HTTPStatusCode", 200)

    _check_missing_field(resp, "SecretList")

    for secret in resp["SecretList"]:
        _check_missing_field(secret, "Name")
        db_secret = get_db_secret_from_secret_name(secret["Name"])

        db_id = db_secret.get(
            # super annoying they didn't name it consistently
            DB_IDENTIFIER_KEY.replace("dBInstance", "dbInstance"))
        if not db_id:
            LOGGER.warning("No database ID fetched from secret name")
            continue
        
        if db_id == db_identifier:
            LOGGER.info("Found matching secret for the database")
            _check_missing_field(secret, "ARN")
            ret_dict = db_secret
            break

    sm_client.close()
    return ret_dict
    

def _get_ddl_source_file_contents(client, bucket, filename):
    """Fetch the contents of the DDL SQL file

    :param client: boto3 Client Object (S3)
    :param bucket: String
    :param filename: String

    :raises: Exception

    :rtype String
    """
    resp = client.get_object(Bucket=bucket, Key=filename)

    _check_missing_field(resp, "ResponseMetadata")

    _validate_field(resp["ResponseMetadata"], "HTTPStatusCode", 200)

    _check_missing_field(resp, "Body")
    body_obj = resp["Body"]
    
    return body_obj.read().decode("utf-8")


def lambda_handler(event, context):
    """What executes when the program is run"""
    
    # configure python logger for Lambda
    _configure_logger()
    # silence chatty libraries for better logging
    _silence_noisy_loggers()
    
    msg_attr = _get_sqs_message_attributes(event)
    
    if msg_attr:

        # Because messages remain in the queue
        LOGGER.info(
            f"Deleting message {msg_attr['message_id']} from sqs")
        sqs_client = boto3.client("sqs")
        queue_url = os.environ.get(SQS_QUEUE_ENV_VAR)
        if not queue_url:
            raise MissingEnvironmentVariable(
                f"{SQS_QUEUE_ENV_VAR} environment variable is required")
                
        deletion_resp = sqs_client.delete_message(
            QueueUrl=queue_url, 
            ReceiptHandle=msg_attr["receipt_handle"])
        
        sqs_client.close()

        resp_metadata = deletion_resp.get("ResponseMetadata")
        if not resp_metadata:
            raise Exception(
                "No response metadata from deletion call")
        status_code = resp_metadata.get("HTTPStatusCode")
        
        if status_code == 200:
            LOGGER.info(f"Successfully deleted message")
        else:
            raise Exception("Unable to delete message")

    body = _get_message_body(event)

    _check_missing_field(body, DB_IDENTIFIER_KEY)
    cluster_id = body[DB_IDENTIFIER_KEY]
    LOGGER.info(f"cluster id: {cluster_id}")

    source_s3_bucket = os.environ.get(DDL_SOURCE_BUCKET_ENV_VAR)
    if not source_s3_bucket:
        raise MissingEnvironmentVariable(DDL_SOURCE_BUCKET_ENV_VAR)
    
    if cluster_id.lower() not in source_s3_bucket.lower():
        LOGGER.warning(
            "DDL Source bucket name does not contain database ID. Exiting.")
        return

    # TODO: make more robust
    body_dbname = body.get("databaseName")
    if not body_dbname:
        # Other database engines may have something different
        # this will need some more thought to make it more resilient
        LOGGER.warning(
            "No databaseName found in the CreateDBInstace event body")
        body_dbname = "information_schema"

    env_db_name = os.environ.get(DB_NAME_ENV_VAR)
    if not env_db_name:
        LOGGER.info(
            f"{DB_NAME_ENV_VAR} environment variable is not supplied")
        db_name = body_dbname
    else:
        LOGGER.warning(
            f"{DB_NAME_ENV_VAR} environment variable will be used as dbname")
        db_name = env_db_name
    
    secret_dict = _fetch_secret_for_db(cluster_id)
    if not secret_dict:
        LOGGER.error(
            f"No secret found associated with the cluster: {cluster_id}. Exiting")
        raise Exception

    ddl_source_file = os.environ.get("DDL_SOURCE_FILE_RDS", DDL_FILE)
    
    s3_client = boto3.client("s3")
    file_content_string = _get_ddl_source_file_contents(
        s3_client, source_s3_bucket, ddl_source_file)
    s3_client.close()

    sql_statements = file_content_string.split(";")

    db_session = queries.Session(
        queries.uri(
            secret_dict["host"],
            int(secret_dict["port"]),
            db_name,
            secret_dict["username"],
            secret_dict["password"]
        )
    )

    with db_session as session:
        for sql in sql_statements:
            # get rid of white spaces
            eff_sql = sql.strip(" \n\t")
            LOGGER.info(f"Executing: {eff_sql}")
            if eff_sql:
                results = session.query(eff_sql)
                print(results)
