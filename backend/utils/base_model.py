from django.db import models


class BaseModel(models.Model):
    """
    Base model class that includes common fields for all models.
    
    Attributes:
        id (AutoField): Primary key for the model.
        created_at (DateTimeField): Timestamp when the record was created.
        updated_at (DateTimeField): Timestamp when the record was last updated.
    """
    id = models.AutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
