FROM pytorch/pytorch

WORKDIR /app

COPY /src /app
COPY requirements.txt /app

RUN python -m pip install --upgrade pip

RUN python -m pip install -r requirements.txt

ENTRYPOINT ["python", "main.py"]