#!/usr/bin/env python3
# import packages
import os
import json
import sys
import logging
from time import datetime
import boto3
from google import genai
from google.genai.types import (
    GenerateContentConfig,
    Tool,
    Retrieval,
    VertexRagStore,
)
from google.oauth2 import service_account
# import custom files
from s3Client import MyS3Client
# configure logger
logging.basicConfig(stream=sys.stderr, 
                    level=logging.INFO, 
                    format="%(asctime)-11s [%(levelname)s] %(message)s (%(name)s:%(lineno)d)")
# initialize global variables
class Config:
    configStore = dict()
    @classmethod
    def create(cls):
        if not cls.configStore:
            # create logger
            cls.configStore["logger"] = logging.getLogger(__name__)
            # set up boto3 session
            cls.configStore["logger"].debug(f"Setting up boto3")
            awsProfile = os.environ.get("AWSPROF", None)
            if awsProfile:
                awsSession = boto3.Session(profile_name=awsProfile, region_name="us-east-1")
            else:
                awsSession = boto3.Session(region_name="us-east-1")
            # set up boto3 clients
            ssmClient = awsSession.client("ssm")
            smClient = awsSession.client("secretsmanager")
            # get variables from parameter store
            gcpSecret = ssmClient.get_parameter(Name="/genai/gcpSecret", WithDecryption=True)["Parameter"]["Value"] # aws secret that contains gcp service account key
            gcpProject = ssmClient.get_parameter(Name="/genai/gcpProject", WithDecryption=True)["Parameter"]["Value"] # gcp project where vertex ai resources are enabled
            gcpRegion = ssmClient.get_parameter(Name="/genai/gcpRegion", WithDecryption=True)["Parameter"]["Value"] # gcp region where vertex ai resources are enabled
            cls.configStore["geminiModel"] = ssmClient.get_parameter(Name="/genai/geminiModel", WithDecryption=True)["Parameter"]["Value"] # gemini model used for chatbot
            s3Bucket = ssmClient.get_parameter(Name="/genai/s3Bucket", WithDecryption=True)["Parameter"]["Value"] # aws bucket for chat history
            corpusId = ssmClient.get_parameter(Name="/genai/corpusId/test", WithDecryption=True)["Parameter"]["Value"] # test corpus id for RAG
            #corpusId =  ssmClient.get_parameter(Name="/genai/corpusId/prod", WithDecryption=True)["Parameter"]["Value"] # prod corpus id for RAG
            # create MyS3ChatHistory instance
            cls.configStore["logger"].debug(f"Setting up s3 client")
            cls.configStore["s3Client"] = MyS3Client(awsSession, s3Bucket)
            # retrieve gcp service account key from aws secrets manager
            cls.configStore["logger"].debug(f"Getting service account key from AWS Secrets Manager")
            saResponse = smClient.get_secret_value(SecretId=gcpSecret)
            saInfo = json.loads(saResponse["SecretString"])
            # authenticate with gcloud service account
            cls.configStore["logger"].debug(f"Authenticating GCP service account")
            credentials = service_account.Credentials.from_service_account_info(
                saInfo,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            # set up genai client
            cls.configStore["logger"].debug(f"Setting up genai client")
            cls.configStore["genaiClient"] = genai.Client(vertexai=True, project=gcpProject, location=gcpRegion, credentials=credentials)
            # set up memory model
            memoryInstruction = """
                You are a memory extractor.
                Your job is to read a chat log between a user and a journaling chatbot. Try to extract concise, standalone journal-style memory entries about the user.
                Each memory entry should encapsulate a day. These may include:
                - What the user did
                - Where they were
                - Who they interacted with
                - How they felt
                That is, facts, events, relationships, or experiences relevant for building a memory profile.

                Please provide the memory as a single, coherent paragraph. Do not use bullet points, line breaks, numbers, or other list markers.
                Exclude instructions, questions, small talk, and any information not relevant to journaling or building a user profile.
                If no relevant memories are present, respond with an empty string.
                The chat log may not always include an exact date (e.g. July 9th 2025) but may include things that could suggest when they occured (e.g. Today, Last Sunday).
                Since these chat logs are from today, you can use these relative date indicators to determine the date of the entry.

                Example output (exactly like this, a single paragraph):
                I went to the park on February 2nd and saw my friend Alex. The weather was sunny and warm. I also, recently started learning Python programming.
                """
            # set up rag
            corpus = VertexRagStore(
                rag_corpora=[corpusId],
                similarity_top_k=10,
                vector_distance_threshold=0.5,
            )
            ragTool = Tool(
                retrieval=Retrieval(vertex_rag_store=corpus)
            )
            cls.configStore["memoryConfig"] = GenerateContentConfig(
                system_instruction=memoryInstruction,
                temperature=0.2, # [float, 0.0, 1.0] control randomness/creativity of response (0.0 is more deterministic, 1.0 is more liberal)
                    top_p=0.9, # [float, 0.0, 1.0] consider min set of most probable tokens that exceed cumulative probability, p, and then randomly pick from the next token from this smaller set
                    top_k=20, # [int, 1, 1000] consider the k most probable next tokens and then randomly pick from the next token from this smaller set
                    max_output_tokens=512, # [int, 1, inf] limit maximum tokens contained in response
                    presence_penalty=0.0, # [float, -2.0, 2.0] negative values discourage use of new tokens while positive values encourage use of new tokens
                    frequency_penalty=0.0, # [float, -2.0, 2.0] negative values encourage repetition of tokens while positive values discourage repetition of tokens
                    tools=[ragTool]
                )
        return cls.configStore
config = Config.create()
# define function to extract relevant info to store to memory
def extract_memories(userInput: str):
    config["logger"].debug("Getting keys for all user chat histories")
    chatKeys = config["s3Client"].obj_list("chat-history/")
    for chatKey in chatKeys:
        user = chatKey.split("/")[-1].split(".")[0]
        config["logger"].debug(f"Now working on {user}'s chat history...")
        config["logger"].debug("Getting chat history from s3")
        chatObj = config["s3Client"].obj_read(chatKey)
        chatJson = json.loads(chatObj)
        config["logger"].debug("Summarizing chat history")
        chatHistory = " ".join(
            part
            for message in chatJson
            for part in message["parts"]
        )
        today = datetime.today().strftime("%B %d, %Y")
        chatWithContext = f"This chat occurred on {today}. {chatHistory}"
        response = config["genaiClient"].models.generate_content(
            model=config["geminiModel"],
            contents=[{"role": "user", "parts": [{"text": chatWithContext}]}],
            config=config["memoryConfig"]
        )