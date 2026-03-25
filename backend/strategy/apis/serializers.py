from rest_framework import serializers
from strategy.models import (
    TbStrategyTemplate
)
from masters.models import (Attribute, AttributeValue)


class AttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attribute
        fields = '__all__'


class AttributeValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeValue
        fields = '__all__'

class TbStrategyTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TbStrategyTemplate
        fields = '__all__'
        
# OLD
# class ProductSerializer(serializers.ModelSerializer):
#     attributes = ProductAttributeSerializer(read_only=True, many=True)

#     class Meta:
#         model = Product
#         fields = '__all__'
#         depth = 5
