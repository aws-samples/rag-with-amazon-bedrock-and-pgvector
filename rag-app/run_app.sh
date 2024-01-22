#!/bin/bash
set -e

echo "initializing app.."
python3 app_init.py

echo "starting streamlit app"
streamlit run app.py bedrock_claude
