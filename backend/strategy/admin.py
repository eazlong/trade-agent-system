from django.contrib import admin
from .models import (TbStrategyTemplate,
                     )

# class ProductSpecificationInline(admin.TabularInline):
#     model = ProductSpecification


@admin.register(TbStrategyTemplate)
class TbStrategyTemplateAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'content', 'params')
    # inlines = [
    #     TbNotifyTemplate
    # ]


# class ProductImageInline(admin.TabularInline):
#     model = ProductImage


# class ProductSpecificationValueInline(admin.TabularInline):
#     model = ProductSpecificationValue
