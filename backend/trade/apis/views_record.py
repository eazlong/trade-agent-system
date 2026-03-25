from math import log
from qtcore.trade_helper_manager import TradeHelperManager
from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from qtcore.wss_data_consumer_manager import WSSDataProcessorManager
from rest_framework.pagination import PageNumberPagination

import logging

from qtbot.apis.serializers import TbUserBotConfigSerializer
from qtbot.models import TbUserBotConfig
from strategy.models import TbStrategyTemplate

class BotOperatoerViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    def destroy(self, request, pk=None):
        user = request.user
        config = TbUserBotConfig.objects.get(id=pk)
        config.template = TbStrategyTemplate.objects.get(id=config.template_id)
        
        strategy = WSSDataProcessorManager().get_consumers(config.template.name)
        if strategy:
            p = strategy.get_register(user.id, config.bot_name)
            p.close_all()
        
        return Response(status=204)


class BotConfigViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request, pk=None):
        user = request.user
        configs = TbUserBotConfig.objects.filter(user_id=user.id)
        for config in configs:
            config.template = TbStrategyTemplate.objects.get(id=config.template_id)
        serializer = TbUserBotConfigSerializer(configs, many=True)
        return Response(serializer.data)

    def create(self, request):
        user = request.user
        data=request.data
        logging.info(data)
        data['user_id'] = user.id
        serializer = TbUserBotConfigSerializer(data=data)
        if serializer.is_valid():
            config = serializer.save()

            from qtcore import qt_bot
            trader = TradeHelperManager().helper(user.id)
            Bot = getattr(qt_bot, data['bot_name']+"Bot", trader)
            WSSDataProcessorManager().get_consumers(data['template']['name']).register(user.id, Bot(user, trader, config))

            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

    def update(self, request, pk=None):
        user = request.user
        instance = TbUserBotConfig.objects.get(id=pk, deleted=False)
        serializer = TbUserBotConfigSerializer(instance, data=request.data, partial=True)
        if serializer.is_valid():
            config = serializer.save()
            
            template = TbStrategyTemplate.objects.get(id=instance.template_id)
            instance.template = template
            stragy = WSSDataProcessorManager().get_consumers(template.name)
            if stragy:
                p = stragy.get_register(user.id, instance.bot_name)
                p.setConfig(config)
            return Response(serializer.data)
        return Response(serializer.errors, status=400)
    
    def destroy(self, request, pk=None):
        user = request.user
        try:
            instance = TbUserBotConfig.objects.get(id=pk, deleted=False)
            if instance.user_id != user.id:
                return Response({'error': 'Not found'}, status=404)
            
            strategy = TbStrategyTemplate.objects.get(id=instance.template_id)
            instance.template = strategy
            WSSDataProcessorManager().get_consumers(strategy.name).unregister(user.id, instance.bot_name)
            
            # Set the deleted field to True instead of deleting
            instance.deleted = True
            instance.save()
            
            return Response(status=204)
        
        except TbUserBotConfig.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

from ..models import TbUserTradeRecord
from .serializers import TradeRecordSerializer

class TradeRecordViewSet(ViewSet):
    permission_classes = [IsAuthenticated]
    def list(self, request, pk):
            user = request.user
            bot_id = int(pk)
            symbol = request.query_params.get('symbol', None)
            logging.info(f'get bot {bot_id}\'s records')
            
            from django.db.models import F
            if bot_id == -1:
                configs = TbUserTradeRecord.objects.filter(user_id=user.id, bot_id=bot_id, parent_order_id=F('order_id'))
            elif bot_id == -999:
                configs = TbUserTradeRecord.objects.filter(bot_id=bot_id, parent_order_id=F('order_id'))
            else:
                configs = TbUserTradeRecord.objects.filter(user_id=user.id, bot_id=bot_id)
            if symbol:
                configs = configs.filter(symbol=symbol)
            
            # Handle sorting
            sort = request.query_params.get('sort')
            if sort:
                sort_fields = sort.split(',')
                # Validate sort fields against model fields
                valid_fields = [f.name for f in TbUserTradeRecord._meta.fields]
                validated_sort = [
                    field for field in sort_fields 
                    if field.lstrip('-') in valid_fields
                ]
                if validated_sort:
                    configs = configs.order_by(*validated_sort)
            
            # Set up pagination
            paginator = PageNumberPagination()
            paginator.page_size = 10  # Set the page size as needed
            paginated_configs = paginator.paginate_queryset(configs, request)
            
            serializer = TradeRecordSerializer(paginated_configs, many=True)
            serialized_data = serializer.data
            for item in serialized_data:
                parent_orders = TbUserTradeRecord.objects.filter(parent_order_id=item['order_id']).order_by('-timestamp').all()
                item['parent_order'] = TradeRecordSerializer(parent_orders, many=True).data

            return paginator.get_paginated_response(serialized_data)

    def retrieve(self, request, pk=None):
        user = request.user
        record = TbUserTradeRecord.objects.get(order_id=pk, user_id=user.id)
        serializer = TradeRecordSerializer(record)

        order = TbUserTradeRecord.objects.filter(parent_order_id=pk).order_by('-timestamp').all()
        
        # 将serializer.data转换为可修改的字典
        response_data = dict(serializer.data)
        # 添加子订单数据
        response_data['parent_order'] = TradeRecordSerializer(order, many=True).data
        
        return Response(response_data)

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from trade.models import TradeRecordComment, TbUserTradeRecord
from trade.apis.serializers import (
    TradeRecordSerializer,
    TradeRecordCommentSerializer,
)
from django.db.models import Q
from django.shortcuts import get_object_or_404

# class TbUserBotConfigViewSet(viewsets.ModelViewSet):
#     queryset = TbUserBotConfig.objects.all()
#     serializer_class = TbUserBotConfigSerializer
#     permission_classes = [IsAuthenticated]

#     def get_queryset(self):
#         return TbUserBotConfig.objects.filter(user_id=self.request.user.id)

#     def perform_create(self, serializer):
#         serializer.save(user_id=self.request.user.id)


class TbUserTradeRecordViewSet(viewsets.ModelViewSet):
    queryset = TbUserTradeRecord.objects.all()
    serializer_class = TradeRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = TbUserTradeRecord.objects.filter(user_id=self.request.user.id)
        bot_id = self.request.query_params.get('bot_id', None)
        symbol = self.request.query_params.get('symbol', None)
        ordering = self.request.query_params.get('ordering', '-timestamp')

        if bot_id is not None:
            if bot_id == '-1':
                # All records
                pass
            elif bot_id == '-999':
                # Bitlang records (bot_id > 900)
                queryset = queryset.filter(bot_id__gt=900)
            else:
                queryset = queryset.filter(bot_id=bot_id)

        if symbol is not None:
            queryset = queryset.filter(symbol=symbol)

        return queryset.order_by(ordering)

    @action(detail=True, methods=['get'])
    def comments(self, request, pk=None):
        """Get all top-level comments for a trade record"""
        
        record = TbUserTradeRecord.objects.get(id=pk)
        logging.info(f"record: {record.bot_id}")
        # Only allow comments for bot_id > 900
        if record.bot_id >= -900:
            return Response({"detail": "Comments not allowed for this record"}, status=status.HTTP_403_FORBIDDEN)
            
        # Get all top-level comments (no parent)
        comments = TradeRecordComment.objects.filter(
            trade_record_id=record.id,
            parent_comment_id__isnull=True
        ).filter(
            # Show public comments or user's own comments
            Q(is_public=True) | Q(user_id=request.user.id)
        )
        logging.info(f"comments: {comments}")
        serializer = TradeRecordCommentSerializer(comments, many=True)
        return Response(serializer.data)


class TradeRecordCommentViewSet(viewsets.ModelViewSet):
    queryset = TradeRecordComment.objects.all()
    serializer_class = TradeRecordCommentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        # Users can see their own comments and public comments
        return TradeRecordComment.objects.filter(
            Q(user_id=self.request.user.id) | Q(is_public=True)
        )
    
    def perform_create(self, serializer):
        # Check if the trade record exists and has bot_id > 900
        trade_record_id = self.request.data.get('trade_record_id')
        trade_record = get_object_or_404(TbUserTradeRecord, id=trade_record_id)
        
        if trade_record.bot_id > -900:
            return Response(
                {"detail": "Comments can only be added to records with bot_id < -900"}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if parent comment exists and is only one level deep
        parent_id = self.request.data.get('parent_comment_id')
        if parent_id:
            parent = get_object_or_404(TradeRecordComment, id=parent_id)
            if parent.parent_comment_id is not None:
                return Response(
                    {"detail": "Cannot reply to a reply. Maximum nesting is 2 levels."}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Save with current user ID
        serializer.save(
            user_id=self.request.user.id,
            username=self.request.user.username
        )
    
    def update(self, request, *args, **kwargs):
        comment = self.get_object()
        # Only allow users to update their own comments
        if comment.user_id != request.user.id:
            return Response(
                {"detail": "You can only edit your own comments"}, 
                status=status.HTTP_403_FORBIDDEN
            )
        return super().update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        comment = self.get_object()
        # Only allow users to delete their own comments
        if comment.user_id != request.user.id:
            return Response(
                {"detail": "You can only delete your own comments"}, 
                status=status.HTTP_403_FORBIDDEN
            )
        return super().destroy(request, *args, **kwargs)
