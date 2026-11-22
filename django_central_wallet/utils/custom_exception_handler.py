# custom handler
from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException, ErrorDetail

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    
    if response is not None:
        if not isinstance(response.data, dict):  # e.g. ValidationError(['...']) carries a list
            response.data = {'errors': response.data}
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

class InvalidAddressException(CustomException):
    default_detail = ErrorDetail('invalid_address', 'invalid_address')

class InvalidAmountException(CustomException):
    default_detail = ErrorDetail('invalid_amount', 'invalid_amount')

class AmountBelowMinimumException(CustomException):
    default_detail = ErrorDetail('amount_below_minimum', 'amount_below_minimum')

class InsufficientBalanceException(CustomException):
    default_detail = ErrorDetail('insufficient_balance', 'insufficient_balance')

class DailyLimitExceededException(CustomException):
    default_detail = ErrorDetail('daily_limit_exceeded', 'daily_limit_exceeded')

class WithdrawalNotAllowedException(CustomException):
    default_detail = ErrorDetail('withdrawal_not_allowed', 'withdrawal_not_allowed')

class InvalidFilterException(CustomException):
    default_detail = ErrorDetail('invalid_filter', 'invalid_filter')

class InvalidCredentialsException(CustomException):
    default_detail = ErrorDetail('invalid_credentials', 'invalid_credentials')

class EmailNotVerifiedException(CustomException):
    default_detail = ErrorDetail('email_not_verified', 'email_not_verified')

class AccountDisabledException(CustomException):
    default_detail = ErrorDetail('account_disabled', 'account_disabled')

class InvalidTokenException(CustomException):
    default_detail = ErrorDetail('invalid_token', 'invalid_token')

class RecipientNotFoundException(CustomException):
    default_detail = ErrorDetail('recipient_not_found', 'recipient_not_found')

class SelfTransferException(CustomException):
    default_detail = ErrorDetail('cannot_transfer_to_self', 'cannot_transfer_to_self')
