from django.contrib import admin
from .models import (
                     TbUserNotifyConfig
                     )


# class ProductSpecificationInline(admin.TabularInline):
#     model = ProductSpecification


# class ProductImageInline(admin.TabularInline):
#     model = ProductImage


# class ProductSpecificationValueInline(admin.TabularInline):
#     model = ProductSpecificationValue


@admin.register(TbUserNotifyConfig)
class TbUserNotifyConfigAdmin(admin.ModelAdmin):
    list_display = ('user_id', 'id', 'params', 'repeat', 'expire', 'template_id')

    # inlines = [
    #     ProductSpecificationValueInline,
    #     ProductImageInline,
    # ]
