# custom handler
from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException, ErrorDetail

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    
    if response is not None:
        response.status_code = 200
        print(response.data)
        response.data['success'] = False
        response_default_detail = response.data.pop('detail', None)
        if response_default_detail is not None:
            try:
                response.data['error'] = response_default_detail.code
                response.data['error_content'] = response_default_detail
            except Exception as e:
                print(f'Exception handler: {e}, {response_default_detail}')
                response.data['error'] = response_default_detail
    return response

class CustomException(APIException):
    status_code = 200

class MissingRerquiredDataException(CustomException):
    default_detail = ErrorDetail('missing_required_data', 'missing_required_data')

class UnknowErrorException(CustomException):
    default_detail = ErrorDetail('unknow_error', 'unknow_error')

class TryAgainLaterException(CustomException):
    default_detail = ErrorDetail('try_again_later', 'try_again_later')


class EmailFormatException(CustomException):
    default_detail = ErrorDetail('email_format_error', 'email_format_error')

class UserAlreadyExistsException(CustomException):
    default_detail = ErrorDetail('user_already_exists', 'user_already_exists')