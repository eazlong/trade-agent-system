from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import ExchangeAccount
from .serializers import ExchangeAccountSerializer


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def account_list(request):
    if request.method == 'GET':
        accounts = ExchangeAccount.objects.filter(is_active=True)
        return Response(ExchangeAccountSerializer(accounts, many=True).data)
    serializer = ExchangeAccountSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def account_detail(request, pk):
    account = ExchangeAccount.objects.get(pk=pk)
    account.is_active = False
    account.save(update_fields=['is_active'])
    return Response(status=status.HTTP_204_NO_CONTENT)
