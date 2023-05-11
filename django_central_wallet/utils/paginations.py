from rest_framework import pagination
from rest_framework.response import Response
from django.http import JsonResponse
from collections import OrderedDict

class CustomPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50
    page_query_param = 'page'

    def get_paginated_response(self, data, key, new_dict=None):
        result = {'success': True,
                'count': self.page.paginator.count,
                'next': True if self.get_next_link() is not None else False,
                key: data}
        if new_dict is not None:
            result.update(new_dict)
        response = Response(result)
        return response