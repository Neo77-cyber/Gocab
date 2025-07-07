import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from .models import RideRequest, Driver
from django.core.serializers.json import DjangoJSONEncoder
from .encoders import DjangoSafeJSONEncoder

logger = logging.getLogger(__name__)





class RideUpdatesConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]
        if self.user.is_anonymous:
            await self.close()
        else:
            await self.accept()
            logger.info(f"RideUpdates WebSocket connected for user {self.user.id}")

    async def disconnect(self, close_code):
        if hasattr(self, 'ride_group'):
            await self.channel_layer.group_discard(
                self.ride_group,
                self.channel_name
            )
            logger.info(f"Disconnected from ride group: {self.ride_group}")

    async def receive(self, text_data):
        """
        Receives subscription requests from the rider frontend.
        Expected payload: {"action": "subscribe", "ride_id": 71}
        """
        try:
            data = json.loads(text_data)
            if data.get('action') == 'subscribe' and data.get('ride_id'):
                ride_id = data['ride_id']
                self.ride_group = f"ride_{ride_id}"
                await self.channel_layer.group_add(
                    self.ride_group,
                    self.channel_name
                )
                logger.info(f"User {self.user.id} subscribed to ride group {self.ride_group}")
        except Exception as e:
            logger.error(f"Error processing subscription: {str(e)}")

    async def ride_update(self, event):
        """
        Called when a message is sent to the 'ride_<ride_id>' group
        """
        try:
            response = {
                'type': 'ride_update',
                'event': event.get('event'),
                'ride_id': event.get('ride_id'),
                'driver': event.get('driver'),
                'eta': event.get('eta'),
                'distance': float(event.get('distance', 0)) if event.get('distance') is not None else None,
                'fare': float(event.get('fare', 0)) if event.get('fare') is not None else None,
                # You can extend this with more ride data as needed
            }
            await self.send(text_data=json.dumps(response, cls=DjangoJSONEncoder))
        except Exception as e:
            logger.error(f"Error sending ride update: {str(e)}")


class DriverUpdatesConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]
        if self.user.is_anonymous or not await self.is_driver():
            await self.close()
        else:
            self.driver_group = f"driver_{self.user.id}"
            await self.channel_layer.group_add(
                "available_drivers",
                self.channel_name
            )
            await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'driver_group'):
            await self.channel_layer.group_discard(
                "available_drivers",
                self.channel_name
            )

    async def receive(self, text_data):
        data = json.loads(text_data)
        if data.get('type') == 'heartbeat':
            await self.send(text_data=json.dumps({'type': 'heartbeat_ack'}))

    async def driver_update(self, event):
        try:
            safe_event = json.loads(json.dumps(event, cls=DjangoSafeJSONEncoder))
            await self.send(text_data=json.dumps(safe_event, cls=DjangoSafeJSONEncoder))
        except Exception as e:
            logger.error(f"Error sending driver update: {str(e)}")

    @database_sync_to_async
    def is_driver(self):
        return hasattr(self.user, 'driver')

    @database_sync_to_async
    def get_pending_rides(self):
        """Get serialized pending rides"""
        rides = RideRequest.objects.filter(
            status='pending', 
            driver__isnull=True
        ).order_by('-requested_at')[:10]  # Limit to 10 most recent
        
        return [{
            'id': ride.id,
            'pickup': ride.current_location,
            'destination': ride.destination,
            'distance': float(ride.distance_km) if ride.distance_km else None,
            'fare': float(ride.total_fare) if ride.total_fare else None,
            'requested_at': ride.requested_at.isoformat()
        } for ride in rides]