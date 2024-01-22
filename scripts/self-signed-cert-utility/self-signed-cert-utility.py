import argparse
import json
import logging
import os
import pathlib
import time

import boto3
from OpenSSL import crypto
import validators
from validators import ValidationError


DEFAULT_LOG_LEVEL = logging.INFO
LOGGER = logging.getLogger(__name__)
LOGGING_FORMAT = "%(asctime)s %(levelname)-5.5s " \
                 "[%(name)s]:[%(threadName)s] " \
                 "%(message)s"

CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"

DEFAULT_APP_DOMAIN = "pgvec.rag"


def generate_ssl_keys(key_config):
    """Generate ssl keys

    Solution taken from:
    https://stackoverflow.com/questions/27164354/create-a-self-signed-x509-certificate-in-python

    :param data_dir_obj: DataDir
    :param key_config: Dictionary

    :rtype: Dictionary
    """
    ssl_dir = f"{pathlib.Path.cwd()}/.ssl"
    if not os.path.isdir(ssl_dir):
        os.mkdir(ssl_dir)
        LOGGER.info("Created ssl dir: %s", ssl_dir)
    else:
        LOGGER.info(f"ssl dir: {ssl_dir} already exists")

    ssl_cert = f"{ssl_dir}/{os.environ.get('CERT_FILE', CERT_FILE)}"
    ssl_pem = f"{ssl_dir}/{os.environ.get('KEY_FILE', KEY_FILE)}"

    LOGGER.debug("Creating a key pair")
    ssl_key = crypto.PKey()
    ssl_key.generate_key(crypto.TYPE_RSA, 2048)

    LOGGER.debug("Creating a self-signed cert")
    cert = crypto.X509()
    cert.get_subject().countryName = key_config["country"]
    cert.get_subject().stateOrProvinceName = key_config["state"]
    cert.get_subject().localityName = key_config["locality"]
    cert.get_subject().organizationName = key_config["organization"]
    cert.get_subject().organizationalUnitName = \
        key_config["organizational_unit"]
    domain = os.environ.get("APP_DOMAIN", DEFAULT_APP_DOMAIN)
    if not domain:
        raise Exception("Missing 'APP_DOMAIN' environement variable'")
    try:
        valid_domain = validators.domain(domain)
        if not valid_domain:
            LOGGER.error(f"'{domain}' could not be validated as a domain")
            raise Exception
    except ValidationError as ve:
        LOGGER.error(f"'{domain}' could not be validated as a domain")
        raise Exception

    cert.get_subject().commonName = domain

    cert.set_serial_number(1000)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(10*365*24*60*60)
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(ssl_key)
    cert.sign(ssl_key, "sha1")

    cert_body = crypto.dump_certificate(
        crypto.FILETYPE_PEM, cert).decode("utf-8")
    LOGGER.info("Writing self-signed cert to: %s", ssl_cert)
    with open(ssl_cert, "wt") as cert_writer:
        cert_writer.write(cert_body)

    key_body = crypto.dump_privatekey(
        crypto.FILETYPE_PEM, ssl_key).decode("utf-8")
    LOGGER.info("Writing self-signed cert to: %s", ssl_pem)
    with open(ssl_pem, "wt") as key_writer:
        key_writer.write(key_body)
    return {
        "key": key_body,
        "cert": cert_body 
    }
    

def _validate_config_file_path(file_path):
    """Checks if passed in file path is valid or not

    :file_path: String

    :raises: FileNotFoundError
    """
    LOGGER.info(f"Config file path: {file_path}")
    if not os.path.isfile(file_path):
        LOGGER.error("Config file provided is not found")
        raise FileNotFoundError
    else:
        LOGGER.debug("File path is valid")
    return


def _parse_key_details_file(args):
    """Parse json file containing key details

    :param args: argparse.Namespace (CLI args)

    :rtype: List <Dictionary>
    """
    absolute_file_path = pathlib.Path(args.config_file).resolve()
    _validate_config_file_path(absolute_file_path)
            
    with open(absolute_file_path) as config_file:
        try:
            return json.load(config_file)
        except json.JSONDecodeError as e:
            LOGGER.error("Configuration file is not valid JSON")
            raise TypeError


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


def _cli_args():
    """Parse CLI Args
    
    :rtype: argparse.Namespace
    """
    parser = argparse.ArgumentParser(description="self-signed-cert-utility")
    parser.add_argument("-p",
                        "--aws-profile",
                        type=str,
                        default="default",
                        help="AWS profile to be used for the API calls")
    parser.add_argument("-f",
                        "--config-file",
                        type=str,
                        default="default_cert_params.json",
                        help="path to configuration file")
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

    cert_name = os.environ.get("IAM_SELF_SIGNED_SERVER_CERT_NAME")
    if not cert_name:
        LOGGER.error(
            "Need to export the 'IAM_SELF_SIGNED_SERVER_CERT_NAME' env var")
        raise Exception

    key_config = _parse_key_details_file(args)

    cert_files = generate_ssl_keys(key_config)
    
    iam_client = boto3.client("iam")

    resp = iam_client.upload_server_certificate(
        ServerCertificateName=cert_name,
        CertificateBody=cert_files["cert"],
        PrivateKey=cert_files["key"]
    )
    _check_missing_field(resp, "ResponseMetadata")
    _validate_field(resp["ResponseMetadata"], "HTTPStatusCode", 200)
    print(resp)

    LOGGER.debug("Closing iam client")
    iam_client.close()

    LOGGER.info(f"Total time elapsed: {time.time() - start} seconds")


if __name__ == "__main__":
    main()