# your_app_name/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer

import logging

from asgiref.sync import async_to_sync, sync_to_async
import time

class NotifyConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        from rest_framework_simplejwt.tokens import AccessToken
        from django.contrib.auth import get_user_model
        User = get_user_model()
        token = AccessToken(self.scope['subprotocols'][1])
        user_id = token['user_id']
        user = await sync_to_async(User.objects.get)(id=user_id)
        self.group_name = f"{user}_group"
        logging.info(f"{user} group logged")

        if user.is_authenticated:
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )
            await self.accept("authorization")
        else:
            await self.close()

    async def disconnect(self, close_code):
        logging.info(f"websocket disconnect....{close_code}")
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        logging.info(f"receive....{text_data}")
        try:
            data = json.loads(text_data)
            message = data['message']
            time = data['time']
            # Echo the message back to the client
            await self.send(text_data=json.dumps({
                'message': message,
                'time': time
            }))
        except Exception as e:
            logging.exception(e)

    async def send_message(self, event):
        message = event['message']
        time = event['time']
        await self.send(text_data=json.dumps({
            'message': message,
            'time': time
        }))