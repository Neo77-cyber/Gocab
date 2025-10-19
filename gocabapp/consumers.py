import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from .models import RideRequest, Driver, Notification
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
                'data': {
                'status': event.get('event'),
                'driver': event.get('driver'),
                'eta': event.get('eta'),
                'fare': float(event.get('fare', 0)) if event.get('fare') is not None else None,
                'distance': float(event.get('distance', 0)) if event.get('distance') is not None else None,
            }
                
                
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
                self.driver_group,
                self.channel_name
            )

            await self.channel_layer.group_add(
                "available_drivers",
                self.channel_name
            )
            await self.accept()

    async def disconnect(self, close_code):

        if hasattr(self, 'driver_group'):
            await self.channel_layer.group_discard(
                self.driver_group,
                self.channel_name
            )

            await self.channel_layer.group_discard(
                "available_drivers",
                self.channel_name
            )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if data.get('type') == 'heartbeat':
                await self.send(text_data=json.dumps({'type': 'heartbeat_ack'}))
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {str(e)}")
    


    # Handle ride accepted event
    async def ride_accepted(self, event):
        """Handle ride.accepted event from channel layer"""
        try:
            response = {
                'type': 'ride_accepted',
                'event': 'ride_accepted',
                'ride': event.get('ride'),
                'message': 'Ride accepted successfully'
            }
            await self.send(text_data=json.dumps(response, cls=DjangoSafeJSONEncoder))
            logger.info(f"Sent ride_accepted event to driver {self.user.id}")
        except Exception as e:
            logger.error(f"Error sending ride_accepted event: {str(e)}")

    # Handle ride update event
    async def ride_update(self, event):
        """Handle ride_update event from channel layer"""
        try:
            response = {
                'type': 'ride_update',
                'event': event.get('event'),
                'ride_id': event.get('ride_id'),
                'message': event.get('message'),
                'data': event.get('data')
            }
            await self.send(text_data=json.dumps(response, cls=DjangoSafeJSONEncoder))
            logger.info(f"Sent ride_update event to driver {self.user.id}: {event.get('event')}")
        except Exception as e:
            logger.error(f"Error sending ride_update event: {str(e)}")

    # Handle new ride request event
    async def new_ride_request(self, event):
        """Handle new_ride_request event from channel layer"""
        try:
            response = {
                'type': 'new_ride_request',
                'event': 'new_ride_request',
                'ride': event.get('ride'),
                'message': 'New ride request available'
            }
            await self.send(text_data=json.dumps(response, cls=DjangoSafeJSONEncoder))
            logger.info(f"Sent new_ride_request event to driver {self.user.id}")
        except Exception as e:
            logger.error(f"Error sending new_ride_request event: {str(e)}")

    # Generic driver update handler
    async def driver_update(self, event):
        """Handle generic driver_update event from channel layer"""
        try:
            safe_event = json.loads(json.dumps(event, cls=DjangoSafeJSONEncoder))
            await self.send(text_data=json.dumps(safe_event, cls=DjangoSafeJSONEncoder))
            logger.info(f"Sent driver_update event to driver {self.user.id}")
        except Exception as e:
            logger.error(f"Error sending driver_update event: {str(e)}")

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
    
class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]
        if self.user.is_anonymous:
            await self.close()
        else:
            await self.channel_layer.group_add(
                f"notifications_{self.user.id}",
                self.channel_name
            )
            # Send current notification count when connecting
            count = await self.get_notification_count()
            await self.accept()
            await self.send(text_data=json.dumps({
                'type': 'counter',
                'count': count
            }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            f"notifications_{self.user.id}",
            self.channel_name
        )

    async def notification_update(self, event):
        """Handle counter updates"""
        await self.send(text_data=json.dumps({
            'type': 'counter',
            'count': event['count']
        }))
    
    @database_sync_to_async
    def get_notification_count(self):
        return Notification.objects.filter(
            user=self.user,
            is_active=True
        ).count()