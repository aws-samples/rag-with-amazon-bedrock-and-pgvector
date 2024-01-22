FROM python:3.11.2

WORKDIR python-docker

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt && mkdir /root/.streamlit

COPY . .

CMD [ "./run_app.sh" ]
