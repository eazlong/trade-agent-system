from rest_framework import serializers
from trade.models import (
    TbUserTradingConfig,
    TbUserTradeRecord,
    TradeRecordComment,
)
from masters.models import (Attribute, AttributeValue,)


class AttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attribute
        fields = '__all__'


class AttributeValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeValue
        fields = '__all__'


class TbUserTradingConfigSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = TbUserTradingConfig
        fields = '__all__'
        # depth = 5

        
class TradeRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = TbUserTradeRecord
        fields = '__all__'

class TradeRecordCommentSerializer(serializers.ModelSerializer):
    replies = serializers.SerializerMethodField()
    user_id = serializers.IntegerField(required=False)
    class Meta:
        model = TradeRecordComment
        fields = '__all__'
    
    def get_replies(self, obj):
        # Only get direct replies (one level deep)
        replies = TradeRecordComment.objects.filter(parent_comment_id=obj.id)
        return TradeRecordCommentSerializer(replies, many=True).data

# OLD
# class ProductSerializer(serializers.ModelSerializer):
#     attributes = ProductAttributeSerializer(read_only=True, many=True)

#     class Meta:
#         model = Product
#         fields = '__all__'
#         depth = 5
