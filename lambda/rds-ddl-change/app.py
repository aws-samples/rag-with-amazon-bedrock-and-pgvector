import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError
import queries


DB_NAME_ENV_VAR = "DB_NAME"
REGION_ENV_VAR = "AWS_REGION"
DDL_SOURCE_BUCKET_ENV_VAR = "DDL_SOURCE_BUCKET"

LOGGER = logging.getLogger()

DDL_FILE = "rds-ddl.sql"

DB_IDENTIFIER_KEY = "dBInstanceIdentifier"

MALFORMED_EVENT_LOG_MSG = "Malformed event. Skipping this record."


class MalformedEvent(Exception):
    """Raised if a malformed event received"""
    

class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


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


def lambda_handler(event, context):
    """What executes when the program is run"""
    
    # configure python logger for Lambda
    _configure_logger()
    # silence chatty libraries for better logging
    _silence_noisy_loggers()

    LOGGER.info("Waiting for DDL source to be updated..")
    time.sleep(120)

    ddl_source_file = os.environ.get("DDL_SOURCE_FILE_RDS", DDL_FILE)

    source_s3_bucket = os.environ.get(DDL_SOURCE_BUCKET_ENV_VAR)
    if not source_s3_bucket:
        raise MissingEnvironmentVariable(DDL_SOURCE_BUCKET_ENV_VAR)
    
    db_name = os.environ.get(DB_NAME_ENV_VAR)
    if not db_name:
        raise MissingEnvironmentVariable(DDL_SOURCE_BUCKET_ENV_VAR)
    
    db_arn = ""
    db_id = source_s3_bucket.replace("ddl-source-", "")
    
    rds_client = boto3.client("rds")
    LOGGER.info("Attempting to get db arn from RDS")
    resp = rds_client.describe_db_instances(DBInstanceIdentifier=db_id)
    rds_client.close()
    
    _check_missing_field(resp, "ResponseMetadata")
    _validate_field(resp["ResponseMetadata"], "HTTPStatusCode", 200)

    print(resp)
    _check_missing_field(resp, "DBInstances")
    try:
        db_details = resp["DBInstances"][0]
        _check_missing_field(db_details, "DBInstanceArn")
        db_arn = db_details["DBInstanceArn"]
    except IndexError:
        LOGGER.error("No databases returned from the API call")

    if not db_arn:
        LOGGER.warning("Unable to fetch database arn from RDS API call."
                       " Will attempt to infer it.")
        region = os.environ.get(REGION_ENV_VAR)
        if not region:
            raise MissingEnvironmentVariable(REGION_ENV_VAR)
        
        account_id = boto3.client('sts').get_caller_identity().get('Account')
        if not account_id:
            LOGGER.warning("Unable to fetch account_id from sts")
        else:
            db_arn = f"arn:aws:rds:{region}:{account_id}:db:{db_id}"
    
    if not db_arn:
        LOGGER.error("Unable to find a matching db ARN. Exiting.")
        raise Exception
    
    LOGGER.info(f"DB ARN: {db_arn}")
    
    secret_dict = _fetch_secret_for_db(db_id)
    if not secret_dict:
        LOGGER.error(
            f"No secret found associated with the db: {db_id}. Exiting")
        raise Exception
    
    s3_client = boto3.client("s3")
    file_content_string = _get_ddl_source_file_contents(
        s3_client, source_s3_bucket, ddl_source_file)
    s3_client.close()

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
        for sql in file_content_string.split(";"):
            # get rid of white spaces
            eff_sql = sql.strip(" \n\t")
            LOGGER.info(f"Executing: {eff_sql}")
            if eff_sql:
                results = session.query(eff_sql)
                print(results)
