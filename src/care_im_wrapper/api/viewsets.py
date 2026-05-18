from rest_framework.viewsets import GenericViewSet
from rest_framework.response import Response
from rest_framework.decorators import action


class BaseViewSet(GenericViewSet):

    @action(detail=False, methods=["get"])
    def hello(self, request, *args, **kwargs):
        return Response({"message": "Hello from care_im_wrapper plugin!"})