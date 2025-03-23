class EchoUUIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        uuid = request.headers.get('X-Operation-UUID')
        response = self.get_response(request)

        if uuid:
            response['X-Operation-UUID'] = uuid
        return response
