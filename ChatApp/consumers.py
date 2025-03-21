import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from ChatApp.models import *
import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool
import os

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\malis\OneDrive\Desktop\Shrujan\Markytics\audio_to_text\verdant-lattice-425012-f6-189acb3a46c1.json"


PROJECT_ID = "verdant-lattice-425012-f6"
display_name = "test_corpus"
paths = ["https://drive.google.com/file/d/1xr8AAajR8BrVL0O97tB9TJbzcBA3WxKh/view?usp=sharing"]

vertexai.init(project=PROJECT_ID, location="us-central1")

embedding_model_config = rag.EmbeddingModelConfig(
    publisher_model="publishers/google/models/text-embedding-004"
)

rag_corpus = rag.create_corpus(
    display_name=display_name,
    embedding_model_config=embedding_model_config,
)

rag.import_files(
    rag_corpus.name,
    paths,
    chunk_size=512,
    chunk_overlap=100,
    max_embedding_requests_per_min=900,
)

rag_retrieval_tool = Tool.from_retrieval(
    retrieval=rag.Retrieval(
        source=rag.VertexRagStore(
            rag_resources=[
                rag.RagResource(
                    rag_corpus=rag_corpus.name,
                )
            ],
            similarity_top_k=5,
            vector_distance_threshold=0.5,
        ),
    )
)

rag_model = GenerativeModel(
    model_name="gemini-1.5-flash-001",tools=[rag_retrieval_tool]
)


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """Handle WebSocket connection."""
        self.room_name = f"room_{self.scope['url_route']['kwargs']['room_name']}"
        await self.channel_layer.group_add(self.room_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        await self.channel_layer.group_discard(self.room_name, self.channel_name)

    async def receive(self, text_data):
        """Handle incoming messages from WebSocket."""
        text_data_json = json.loads(text_data)
        message_text = text_data_json['message']
        sender = text_data_json['sender']
        room_name = text_data_json['room_name']

        print("message_text received:", message_text)  # Debugging step

        # ✅ Always store the user's message first
        user_message_data = {
            'sender': sender,
            'message': message_text
        }
        await self.create_message(user_message_data, room_name)  

        # ✅ Broadcast the user message to the frontend
        await self.channel_layer.group_send(
            self.room_name,
            {"type": "send_message", "message": user_message_data}
        )

        if message_text.lower() in ['what is my previous question?', 'what was my last question?', 'last question']:
            last_question = await self.get_last_user_question(room_name, sender)

            # ✅ Bot response including the last question
            bot_response = {
                'sender': "RAG AI",
                'message': f"Your last question was: {last_question}"
            }
        else:
            # ✅ Get RAG response for normal queries
            response_text = await self.get_rag_response(message_text)
            bot_response = {
                'sender': "RAG AI",
                'message': response_text
            }

        # ✅ Save the bot's response to the database
        await self.create_message(bot_response, room_name)

        # ✅ Broadcast bot response to the frontend
        await self.channel_layer.group_send(
            self.room_name,
            {"type": "send_message", "message": bot_response}
        )

    @database_sync_to_async
    def get_last_user_question(self, room_name, sender):
        """Retrieve the last question sent by the user before the current one."""
        messages = Message.objects.filter(
            room__room_name=room_name, sender=sender
        ).order_by("-timestamp")

        print("All messages:", [msg.message for msg in messages])  # Debugging

        if messages.count() > 1:  # Ensure at least two messages exist
            return messages[1].message  # Get second latest message
        else:
            return "I don't remember your last question."

    async def send_message(self, event):
        """Send messages to WebSocket clients."""
        await self.send(text_data=json.dumps({'message': event["message"]}))

    @database_sync_to_async
    def create_message(self, data, room_name):
        """Store messages in the database."""
        room = Room.objects.get(room_name=room_name)
        Message.objects.create(room=room, sender=data['sender'], message=data['message'])

    async def get_rag_response(self, query):
        """Retrieve AI-generated response from Vertex RAG."""
        response = await database_sync_to_async(rag_model.generate_content)(query)
        return response.candidates[0].text  # Ensure correct attribute usage