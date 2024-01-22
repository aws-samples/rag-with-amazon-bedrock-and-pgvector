import logging
import os

import boto3


LOGGER = logging.getLogger()

USER_POOL_ENV_VAR = "USER_POOL_ID"
APP_CLIENT_ENV_VAR = "APP_CLIENT_ID"
ALB_DNS_ENV_VAR = "ALB_DNS_NAME"


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
    

def _extract_valid_event(event):
    """Validate incoming event and extract necessary attributes
    
    :param event: Dictionary
    
    :raises: MalformedEvent
    :raises: ValueError
    
    :rtype: Dictionary
    """

    _validate_field(event, "source", "aws.cognito-idp")
    
    _check_missing_field(event, "detail")
    event_detail = event["detail"]

    _validate_field(
        event_detail, 
        "sourceIPAddress", 
        "cloudformation.amazonaws.com"
    )

    _validate_field(
        event_detail, 
        "eventSource", 
        "cognito-idp.amazonaws.com"
    )
    
    _validate_field(event_detail, "eventName", "UpdateUserPoolClient")

    _check_missing_field(event_detail, "responseElements")
    _check_missing_field(event_detail["responseElements"], "userPoolClient")
    
    return event_detail["responseElements"]["userPoolClient"]


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


def lambda_handler(event, context):
    """What executes when the program is run"""
    
    # configure python logger
    _configure_logger()
    # silence chatty libraries
    _silence_noisy_loggers()

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
    
    client_details = _extract_valid_event(event)
    LOGGER.info("Extracted user pool details")

    expected_callback_url = f"https://{alb_dns}/oauth2/idpresponse"
    lowered_callback_url = expected_callback_url.lower()

    _validate_field(client_details, "userPoolId", user_pool_id)
    _validate_field(client_details, "clientId", app_client_id)

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
