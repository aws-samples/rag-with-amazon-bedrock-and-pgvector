import logging
import os
import sys

from langchain.chains import ConversationalRetrievalChain
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.llms.bedrock import Bedrock
from langchain.prompts import PromptTemplate
from langchain.vectorstores.pgvector import PGVector

import helper_functions as hfn


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


MAX_HISTORY_LENGTH = 5

COLLECTION_ENV_VAR = "COLLECTION_NAME"
DB_SECRET_ENV_VAR = "DB_CREDS"
API_KEY_SECRET_ENV_VAR = "API_KEY_SECRET_NAME"

DEFAULT_LOG_LEVEL = logging.INFO
LOGGER = logging.getLogger(__name__)
LOGGING_FORMAT = "%(asctime)s %(levelname)-5.5s " \
                 "[%(name)s]:[%(threadName)s] " \
                 "%(message)s"


def build_chain(db_creds, collection):
  """Build conversational retrieval chain

  :param db_creds: Dictionary
  :param collection: String

  :rtype: ConversationalRetrievalChain
  """
  region = os.environ["AWS_REGION"]

  llm = Bedrock(
      # credentials_profile_name=credentials_profile_name,
      region_name = region,
      model_kwargs={"max_tokens_to_sample":300,"temperature":1,"top_k":250,"top_p":0.999,"anthropic_version":"bedrock-2023-05-31"},
      model_id=os.environ.get("FOUNDATION_MODEL_ID", "anthropic.claude-instant-v1")
  )
  conn_str = PGVector.connection_string_from_db_params(
     driver=os.environ.get("PGVECTOR_DRIVER", "psycopg2"),
     host=db_creds["host"],
     port=db_creds["port"],
     database=os.environ.get("PGVECTOR_DATABASE", "postgres"),
     user=db_creds["username"],
     password=db_creds["password"],
  )
  embeddings = OpenAIEmbeddings()
  store = PGVector(
    collection_name=collection,
    connection_string=conn_str,
    embedding_function=embeddings,
  )
  retriever = store.as_retriever()      

  prompt_template = """Human: This is a friendly conversation between a human and an AI. 
  The AI is talkative and provides specific details from its context but limits it to 240 tokens.
  If the AI does not know the answer to a question, it truthfully says it 
  does not know.

  Assistant: OK, got it, I'll be a talkative truthful AI assistant.

  Human: Here are a few documents in <documents> tags:
  <documents>
  {context}
  </documents>
  Based on the above documents, provide a detailed answer for, {question} 
  Answer "don't know" if not present in the document. 

  Assistant:
  """
  PROMPT = PromptTemplate(
      template=prompt_template, input_variables=["context", "question"]
  )

  condense_qa_template = """{chat_history}
  Human:
  Given the previous conversation and a follow up question below, rephrase the follow up question
  to be a standalone question.

  Follow Up Question: {question}
  Standalone Question:

  Assistant:"""
  standalone_question_prompt = PromptTemplate.from_template(condense_qa_template)

  return ConversationalRetrievalChain.from_llm(
        llm=llm, 
        retriever=retriever, 
        condense_question_prompt=standalone_question_prompt, 
        return_source_documents=True, 
        combine_docs_chain_kwargs={"prompt":PROMPT},
        verbose=True)


def run_chain(chain, prompt: str, history=[]):
  return chain({"question": prompt, "chat_history": history})


if __name__ == "__main__":

  # logging configuration
  log_level = DEFAULT_LOG_LEVEL
  if os.environ.get("VERBOSE", "").lower() == "true":
     log_level = logging.DEBUG
  logging.basicConfig(level=log_level, format=LOGGING_FORMAT)

  # vector db secret fetch
  secret_name = os.environ.get(DB_SECRET_ENV_VAR)
  if not secret_name:
     raise MissingEnvironmentVariable(f"{DB_SECRET_ENV_VAR} environment variable is required")
  
  # open ai api key fetch
  openai_secret = os.environ.get(API_KEY_SECRET_ENV_VAR)
  if not openai_secret:
     raise MissingEnvironmentVariable(f"{API_KEY_SECRET_ENV_VAR} environment variable is required")
  os.environ["OPENAI_API_KEY"] = hfn.get_secret_from_name(openai_secret, kv=False)
  
  # get collection name
  collection = os.environ.get(COLLECTION_ENV_VAR)
  if not collection:
     raise MissingEnvironmentVariable(f"{COLLECTION_ENV_VAR} environment variable is required")
  
  LOGGER.info("starting conversational retrieval chain now..")
  
  # langchain stuff
  chat_history = []
  qa = build_chain(
     hfn.get_secret_from_name(secret_name), 
     collection
  )
  
  print(bcolors.OKBLUE + "Hello! How can I help you?" + bcolors.ENDC)
  print(bcolors.OKCYAN + "Ask a question, start a New search: or CTRL-D to exit." + bcolors.ENDC)
  print(">", end=" ", flush=True)
  
  for query in sys.stdin:
    if (query.strip().lower().startswith("new search:")):
      query = query.strip().lower().replace("new search:","")
      chat_history = []
    elif (len(chat_history) == MAX_HISTORY_LENGTH):
      chat_history.pop(0)

    result = run_chain(qa, query, chat_history)

    chat_history.append((query, result["answer"]))

    print(bcolors.OKGREEN + result['answer'] + bcolors.ENDC)
    if 'source_documents' in result:
      print(bcolors.OKGREEN + 'Sources:')
      for d in result['source_documents']:
        print(d.metadata['source'])
    print(bcolors.ENDC)
    print(bcolors.OKCYAN + "Ask a question, start a New search: or CTRL-D to exit." + bcolors.ENDC)
    print(">", end=" ", flush=True)
  
  print(bcolors.OKBLUE + "Bye" + bcolors.ENDC)
