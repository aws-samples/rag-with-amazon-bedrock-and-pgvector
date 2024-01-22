import json
import logging
import os

import boto3


LOGGER = logging.getLogger()

USER_POOL_ENV_VAR = "USER_POOL_ID"
APP_CLIENT_ENV_VAR = "APP_CLIENT_ID"
ALB_DNS_ENV_VAR = "ALB_DNS_NAME"
SQS_QUEUE_ENV_VAR = "SQS_QUEUE_URL"


class MalformedEvent(Exception):
    """Raised if a malformed event received"""
    

class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


def _silence_noisy_loggers():
    """Silence chatty libraries for better logging"""
    for logger in ['boto3', 'botocore',
                   'botocore.vendored.requests.packages.urllib3']:
        logging.getLogger(logger).setLevel(logging.WARNING)


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


def lambda_handler(event, context):
    """What executes when the program is run"""
    
    # configure python logger
    _configure_logger()
    # silence chatty libraries
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

    client_details = _get_message_body(event)
    LOGGER.info("Extracted user pool details")

    user_pool_id = os.environ.get(USER_POOL_ENV_VAR)
    if not user_pool_id:
        raise MissingEnvironmentVariable(
            f"{USER_POOL_ENV_VAR} environment variable is required")
    
    app_client_id = os.environ.get(APP_CLIENT_ENV_VAR)
    if not app_client_id:
        raise MissingEnvironmentVariable(
            f"{APP_CLIENT_ENV_VAR} environment variable is required")

    alb_dns = os.environ.get(ALB_DNS_ENV_VAR)
    if not alb_dns:
        raise MissingEnvironmentVariable(
            f"{ALB_DNS_ENV_VAR} environment variable is required")    
    
    _validate_field(client_details, "userPoolId", user_pool_id)
    _validate_field(client_details, "clientId", app_client_id)
    
    expected_callback_url = f"https://{alb_dns}/oauth2/idpresponse"
    lowered_callback_url = expected_callback_url.lower()

    _check_missing_field(client_details, "callbackURLs")
    callback_urls = client_details["callbackURLs"]
    if len(callback_urls) != 1:
        LOGGER.warning("Unexpected number of callback URLs")
    else:
        if callback_urls[0] != expected_callback_url:
            LOGGER.warning(
                "Looks like the callback URL is not "
                "associated with the correct load balancer. Please verify.")
            
    cog_client = boto3.client("cognito-idp")

    LOGGER.info("Updating the user pool client URL")
    resp = cog_client.update_user_pool_client(
        UserPoolId=user_pool_id,
        ClientId=app_client_id,
        ExplicitAuthFlows=client_details["explicitAuthFlows"],
        SupportedIdentityProviders=client_details["supportedIdentityProviders"],
        CallbackURLs=[lowered_callback_url],
        AllowedOAuthFlows=client_details["allowedOAuthFlows"],
        AllowedOAuthScopes=client_details["allowedOAuthScopes"],
        AllowedOAuthFlowsUserPoolClient=client_details["allowedOAuthFlowsUserPoolClient"],
        EnableTokenRevocation=client_details["enableTokenRevocation"],
        EnablePropagateAdditionalUserContextData=client_details["enablePropagateAdditionalUserContextData"],
        AuthSessionValidity=client_details["authSessionValidity"]
    )
    _check_missing_field(resp, "ResponseMetadata")
    resp_metadata = resp["ResponseMetadata"]

    _check_missing_field(resp_metadata, "HTTPStatusCode")
    status_code = resp_metadata["HTTPStatusCode"]
    
    if status_code == 200:
        LOGGER.info("Successfully updated callback URL")
    else:
        raise Exception("Unable to update user pool client")   

    cog_client.close()
