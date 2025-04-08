from flask import Flask, jsonify, request, send_file
from pymongo import MongoClient
import os
from dotenv import load_dotenv
from bson import ObjectId
from google import genai
from google.genai import types
import json
import requests
from pydub import AudioSegment
from flask_cors import CORS
import json_repair
import threading
import datetime
import hashlib
import hmac
from urllib.parse import urlparse, quote

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app) # Enable CORS for all routes and origins
key = os.getenv('AWS_ACCESS_KEY_ID')
secret = os.getenv('AWS_SECRET_ACCESS_KEY')

# Get MongoDB connection string from environment variable
mongo_uri = os.getenv('MONGODB_URI')
client = MongoClient(mongo_uri)

# Select the database and collection
db = client.get_default_database()
podcast_collection = db.podcast
audio_list = []

client = genai.Client(api_key=os.getenv('GOOGLE_API_KEY'))

def create_speech(sentance, speaker, gender, output_file):
    SPEECH_KEY = os.getenv('SPEECH_KEY')  # Replace with your actual speech key
    SPEECH_REGION = "centralindia"

    url = f"https://{SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": SPEECH_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-48khz-192kbitrate-mono-mp3",
        "User-Agent": "requests"
    }

    data = f"""<speak version='1.0' xml:lang='en-US'>
        <voice xml:lang='en-US' xml:gender='{gender}' name='{speaker}'>
            {sentance}
        </voice>
    </speak>""".encode('utf-8')

    response = requests.post(url, headers=headers, data=data)

    if response.status_code == 200:
        with open(output_file, "wb") as f:
            f.write(response.content)
        print(f"Audio saved to {output_file}")
    else:
        print(f"Error: {response.status_code}, {response.text}")

def merge_audio_files(audio_files, output_file):
    combined = AudioSegment.empty()
    for file in audio_files:
        audio = AudioSegment.from_file(file)
        combined += audio
    combined.export(output_file, format="mp3")  # Change format as needed
    print(f"Combined audio saved to {output_file}")

def sign(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def get_signature_key(key, date_stamp, region_name, service_name):
    k_date = sign(('AWS4' + key).encode('utf-8'), date_stamp)
    k_region = sign(k_date, region_name)
    k_service = sign(k_region, service_name)
    return sign(k_service, 'aws4_request')

def create_audio_file(text, id):
    global audio_list
    try:
    # if True:
        os.makedirs(f'audio/{id}', exist_ok=True)

        parsed_json = json.loads(text)
        sections = list(parsed_json.keys())

        count = 0
        output_files = []
    
        for section in sections:
            print("\n", parsed_json[section]['title'], end='\n\n')
            dialogues = parsed_json[section]['dialogues']
            for dialogue in dialogues:
                try:
                    print(dialogue['speaker'].upper(), ':', dialogue['text'].replace('*', ' '))
                    count += 1

                    if dialogue['speaker'].upper() == 'ANYA':
                        speaker = 'en-US-LunaNeural'
                        gender = 'Female'
                        sentance = dialogue['text'].replace('*', ' ')
                        output_file = f"audio/{id}/output{count}.mp3"
                        create_speech(sentance, speaker, gender, output_file)
                    else:
                        speaker = 'en-US-AndrewMultilingualNeural'
                        gender = 'Male'
                        sentance = dialogue['text'].replace('*', ' ')
                        output_file = f"audio/{id}/output{count}.mp3"
                        create_speech(sentance, speaker, gender, output_file)
                    print(speaker, output_file)
                    output_files.append(output_file)
                
                except Exception as e:
                    print(f"Error in dialogue: {e}")
                
        merge_audio_files(output_files, f"audio/{id}/final.mp3") 
        
        # Upload to S3
        TEBI_ENDPOINT_URL = 'https://s3.tebi.io'
        TEBI_REGION = 's3.de.tebi.io'
        BUCKET_NAME = 'podcast'

        file_path = f"audio/{id}/final.mp3"
        object_key = f"{id}.mp3"

        with open(file_path, 'rb') as f:
            file_content = f.read()

        payload_hash = hashlib.sha256(file_content).hexdigest()
        t = datetime.datetime.utcnow()
        amz_date = t.strftime('%Y%m%dT%H%M%SZ')
        date_stamp = t.strftime('%Y%m%d')
        service = 's3'
        algorithm = 'AWS4-HMAC-SHA256'

        host = f"{BUCKET_NAME}.{urlparse(TEBI_ENDPOINT_URL).netloc}"
        canonical_uri = '/' + quote(object_key.lstrip('/'))
        endpoint = f"https://{host}{canonical_uri}"
        signed_headers = 'host;x-amz-content-sha256;x-amz-date'

        canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        canonical_request = f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

        credential_scope = f"{date_stamp}/{TEBI_REGION}/{service}/aws4_request"
        string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"

        signing_key = get_signature_key(secret, date_stamp, TEBI_REGION, service)
        signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

        authorization_header = f"{algorithm} Credential={key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
        headers = {
            'Authorization': authorization_header,
            'x-amz-content-sha256': payload_hash,
            'x-amz-date': amz_date,
            'Content-Length': str(len(file_content)),
        }

        response = requests.put(endpoint, headers=headers, data=file_content)
        if response.ok:
            print("Upload to S3 successful!")
            podcast_collection.update_one(
                {'_id': ObjectId(id)},
                {'$set': {'audio': f"audio/{id}/final.mp3"}}
            )
            audio_list.remove(id)           
        else:
            print(f"S3 upload failed: {response.text}") 

    except Exception as e:
        print(f"Error create audio podcasts : {e}")

@app.route('/')
def hello_world():
    return send_file('index.html')

@app.route('/api/podcasts', methods=['GET'])
def get_podcasts():
    try:
        # Get all documents from podcast collection
        podcasts = list(podcast_collection.find({}))
        # Convert ObjectId to string for each document
        for podcast in podcasts:
            podcast['_id'] = str(podcast['_id'])
        return jsonify(podcasts)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/podcasts/<string:podcast_id>')
def get_podcast(podcast_id):
    try:
        # Get the document with the specified podcast_id
        podcast = podcast_collection.find_one({'_id': ObjectId(podcast_id)})
        if podcast is None:
            return jsonify({'error': 'Podcast not found'}), 404
        # Convert ObjectId to string
        podcast['_id'] = str(podcast['_id'])
        return jsonify(podcast)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/podcasts', methods=['POST'])
def create_podcast():
    # try:
    if True:
        # Get the JSON data from the request body
        body = request.get_json()

        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=body['prompt'],
            config=types.GenerateContentConfig(
            min_output_tokens=2000,
            # max_output_tokens=700,
            system_instruction='''You are a technical script writter for a podcast whose time is more then 30 minutes. 
            Write a script for the following topic with 2 expert anya and ben talking with proper starting and ending. 
            And give the output in the following format:
            {
                "section_1": {
                "title": "Introduction",
                "dialogues": [
                    {
                    "speaker": "Anya",
                    "text": "Hello, I am Anya"
                    },
                    {
                    "speaker": "Ben",
                    "text": "Hello, I am Ben"
                    }, ...
                ]
                }, ...
            }''',
            response_mime_type= 'application/json',
                tools=[
                    types.Tool(
                        google_search=types.GoogleSearch()
                    )
                ]
            )
        )

        # Extract JSON content between code blocks or use the raw text if no code blocks found
        json_str = response.text.split('```')[0].strip()
        parsed_json = json_repair.loads(json_str)
        print(parsed_json)

        try:
            answer = json.dumps(parsed_json[0], indent=4)
        except:
            answer = json.dumps(parsed_json, indent=4)

        podcast = {
            'prompt': body['prompt'],
            'answer': answer
        }

        result = podcast_collection.insert_one(podcast)
        inserted_id = str(result.inserted_id)
        return jsonify({'message': 'Podcast created successfully', 'podcast': inserted_id, "result": answer})
    # except Exception as e:
    #     return jsonify({'error': str(e)}), 500

@app.route('/api/generate_audio/<string:podcast_id>', methods=['POST', 'GET'])
def generate_audio(podcast_id):
    global audio_list

    try:
        if "final.mp3" in os.listdir(f"audio/{podcast_id}"):
            # send the audio file in response
            return send_file(f"audio/{podcast_id}/final.mp3", download_name=f"{podcast_id}.mp3", mimetype="audio/mpeg" ,as_attachment=True)
    except:
        # Directory does not exist, continue with audio generation
        pass

    if podcast_id in audio_list:
        return jsonify({'message': 'Audio generation already in progress'}), 200
    audio_list.append(podcast_id)
    podcast = podcast_collection.find_one({'_id': ObjectId(podcast_id)})
    if podcast is None:
        return jsonify({'error': 'Podcast not found'}), 404
    id = podcast_id
    threading.Thread(target=create_audio_file, args=(podcast['answer'], id)).start()
        # return jsonify({'message': 'Audio generation started'}), 202
    return jsonify({'message': 'Audio generation started'}), 202

if __name__ == '__main__':
    app.run(debug=True)
