FROM public.ecr.aws/lambda/python:3.11

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt

COPY lambda_function.py lambda_function.py

CMD [ "lambda_function.lambda_handler"]
