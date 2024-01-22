import logging
import os
import sys
import uuid

import streamlit as st

import pgvec_chat_bedrock as bedrock_claude


# TODO: clean up the way this app is written
USER_ICON = "images/user-icon.png"
AI_ICON = "images/ai-icon.png"
MAX_HISTORY_LENGTH = 5

COLLECTION_ENV_VAR = "COLLECTION_NAME"
DB_SECRET_ENV_VAR = "DB_CREDS"
API_KEY_SECRET_ENV_VAR = "API_KEY_SECRET_NAME"

DEFAULT_LOG_LEVEL = logging.INFO
LOGGER = logging.getLogger(__name__)
LOGGING_FORMAT = "%(asctime)s %(levelname)-5.5s " \
                 "[%(name)s]:[%(threadName)s] " \
                 "%(message)s"


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


# logging configuration
log_level = DEFAULT_LOG_LEVEL
if os.environ.get("VERBOSE", "").lower() == "true":
    log_level = logging.DEBUG
logging.basicConfig(level=log_level, format=LOGGING_FORMAT)

os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

# get collection name
collection = os.environ.get(COLLECTION_ENV_VAR)
if not collection:
    raise MissingEnvironmentVariable(f"{COLLECTION_ENV_VAR} environment variable is required")

#function to read a properties file and create environment variables
def read_properties_file(filename):
    import os
    import re
    with open(filename, 'r') as f:
        for line in f:
            m = re.match(r'^\s*(\w+)\s*=\s*(.*)\s*$', line)
            if m:
                os.environ[m.group(1)] = m.group(2)


# Check if the user ID is already stored in the session state
if 'user_id' in st.session_state:
    user_id = st.session_state['user_id']

# If the user ID is not yet stored in the session state, generate a random UUID
else:
    user_id = str(uuid.uuid4())
    st.session_state['user_id'] = user_id


if 'llm_chain' not in st.session_state:
    if (len(sys.argv) > 1):
        if (sys.argv[1] == 'bedrock_claude'):
            st.session_state['llm_app'] = bedrock_claude
            st.session_state['llm_chain'] = bedrock_claude.build_chain(
                {
                    "host": st.secrets["host"],
                    "port": int(st.secrets["port"]),
                    "username": st.secrets["username"],
                    "password": st.secrets["password"]
                }, 
                collection
            )
        else:
            raise Exception("Unsupported LLM: ", sys.argv[1])
    else:
        raise Exception("Usage: streamlit run app.py bedrock_claude")

if 'chat_history' not in st.session_state:
    st.session_state['chat_history'] = []
    
if "chats" not in st.session_state:
    st.session_state.chats = [
        {
            'id': 0,
            'question': '',
            'answer': ''
        }
    ]

if "questions" not in st.session_state:
    st.session_state.questions = []

if "answers" not in st.session_state:
    st.session_state.answers = []

if "input" not in st.session_state:
    st.session_state.input = ""


st.markdown("""
        <style>
               .block-container {
                    padding-top: 32px;
                    padding-bottom: 32px;
                    padding-left: 0;
                    padding-right: 0;
                }
                .element-container img {
                    background-color: #000000;
                }

                .main-header {
                    font-size: 24px;
                }
        </style>
        """, unsafe_allow_html=True)


def write_logo():
    col1, col2, col3 = st.columns([5, 1, 5])
    with col2:
        st.image(AI_ICON, use_column_width='always') 


def write_top_bar():
    col1, col2, col3 = st.columns([1,10,2])
    with col1:
        st.image(AI_ICON, use_column_width='always')
    with col2:
        selected_provider = sys.argv[1]
        provider = selected_provider.capitalize()
        header = f"An AI App powered by PGVector (on Amazon RDS) and {provider}!"
        st.write(f"<h3 class='main-header'>{header}</h3>", unsafe_allow_html=True)
    with col3:
        clear = st.button("Clear Chat")
    return clear


clear = write_top_bar()

if clear:
    st.session_state.questions = []
    st.session_state.answers = []
    st.session_state.input = ""
    st.session_state["chat_history"] = []


def handle_input():
    input = st.session_state.input
    question_with_id = {
        'question': input,
        'id': len(st.session_state.questions)
    }
    st.session_state.questions.append(question_with_id)

    chat_history = st.session_state["chat_history"]
    if len(chat_history) == MAX_HISTORY_LENGTH:
        chat_history = chat_history[:-1]

    llm_chain = st.session_state['llm_chain']
    chain = st.session_state['llm_app']
    result = chain.run_chain(llm_chain, input, chat_history)
    answer = result['answer']
    chat_history.append((input, answer))
    
    document_list = []
    if 'source_documents' in result:
        for d in result['source_documents']:
            if not (d.metadata['source'] in document_list):
                document_list.append((d.metadata['source']))

    st.session_state.answers.append({
        'answer': result,
        'sources': document_list,
        'id': len(st.session_state.questions)
    })
    st.session_state.input = ""


def write_user_message(md):
    col1, col2 = st.columns([1,12])
    
    with col1:
        st.image(USER_ICON, use_column_width='always')
    with col2:
        st.warning(md['question'])


def render_result(result):
    answer, sources = st.tabs(['Answer', 'Sources'])
    with answer:
        render_answer(result['answer'])
    with sources:
        if 'source_documents' in result:
            render_sources(result['source_documents'])
        else:
            render_sources([])


def render_answer(answer):
    col1, col2 = st.columns([1,12])
    with col1:
        st.image(AI_ICON, use_column_width='always')
    with col2:
        st.info(answer['answer'])


def render_sources(sources):
    col1, col2 = st.columns([1,12])
    with col2:
        with st.expander("Sources"):
            for s in sources:
                st.write(s)


#Each answer will have context of the question asked in order to associate the provided feedback with the respective question
def write_chat_message(md, q):
    chat = st.container()
    with chat:
        render_answer(md['answer'])
        render_sources(md['sources'])
    
        
with st.container():
  for (q, a) in zip(st.session_state.questions, st.session_state.answers):
    write_user_message(q)
    write_chat_message(a, q)


st.markdown('---')
input = st.text_input("You are talking to an AI, ask any question.", key="input", on_change=handle_input)