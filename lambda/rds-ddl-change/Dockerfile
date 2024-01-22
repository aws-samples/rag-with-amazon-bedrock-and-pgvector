FROM public.ecr.aws/lambda/python:3.11

RUN yum update -y \ 
    && yum install -y postgresql-libs gcc postgresql-devel \
    && pip3 install psycopg2

RUN yum install -y amazon-linux-extras \
    && yum repolist \
    && PYTHON=python2 amazon-linux-extras install postgresql10 -y \
    # && amazon-linux-extras install postgresql10 \
    && pip3 install psycopg2 \
    && pip3 install psycopg2-binary

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt

COPY app.py app.py

CMD [ "app.lambda_handler"]
