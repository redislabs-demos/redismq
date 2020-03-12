import json
import logging
import os
import threading
import uuid

import grpc

from message_queue_pb2 import SendRequest, ReceiveRequest, GetRequest, AcknowledgeRequest
from message_queue_pb2_grpc import MessageQueueStub


def send_message(service_name, func_name, params={}, rsp_to=1):
    """
    Put a message into a message queue.

    :param service_name: The name of the service.
    :param func_name: The name fo the function.
    :param params: The parameters.
    :param rsp_to: An optional timeout (in seconds) to wait for the response, defaults to 1.
    :return: The payload of the response.
    """
    logging.debug(f'sending message to {service_name}.{func_name} ...')
    req_id = MQ.send_req(service_name, func_name, json.dumps({
        "params": params
    }))
    logging.debug(f'receiving response from {service_name}.{func_name} ...')
    rsp = MQ.recv_rsp(service_name, func_name, req_id, rsp_to)
    if not rsp:
        raise TimeoutError('{}.{}'.format(service_name, func_name))

    MQ.ack_rsp(service_name, func_name, req_id, rsp)

    return json.loads(rsp)


class Consumers(object):
    """
    Consumers class.

    Helper class to handle multiple consumers.
    """

    def __init__(self, service_name, handler_funcs, timeout=1):
        """
        :param service_name: A service name.
        :param handler_funcs: A list of handler functions.
        :param timeout: An optional timeout in seconds, defaults to 1.
        """
        self.service_name = service_name
        self.handler_funcs = handler_funcs
        self.timeout = timeout
        self.threads = [threading.Thread(target=self._run,
                                         name='{}.{}'.format(service_name, m.__name__),
                                         args=(m, )) for m in handler_funcs]
        self.running = False

    def start(self):
        """
        Spawn a consumer thread for all handler functions.

        :return: None
        """
        self.running = True
        [t.start() for t in self.threads]

        return self

    def wait(self):
        """
        Wait for all consumer threads to finsih. N.B. This is a blocking operation.

        :return: None
        """
        [t.join() for t in self.threads]

        return self

    def stop(self):
        """
        Stop all consumer threads.

        :return: None
        """
        self.running = False

        return self

    def _run(self, handler_func):

        while self.running:

            logging.debug(f'receiving request in {self.service_name}.{handler_func.__name__} ...')

            req_id, req_payload = MQ.recv_req(self.service_name, handler_func.__name__, self.timeout)
            if not req_payload:
                continue

            try:
                params = json.loads(req_payload)['params']
            except Exception as e:
                rsp = {
                    "error": "Error parsing received payload ({}): {}".format(e.__class__.__name__, str(e))
                }
            else:
                logging.debug(f'calling handler function in {self.service_name}.{handler_func.__name__} ...')
                try:
                    rsp = handler_func(params)
                except Exception as e:
                    rsp = {
                        "error": "Error calling handler function ({}): {}".format(e.__class__.__name__, str(e))
                    }

            MQ.ack_req(self.service_name, handler_func.__name__, req_id)

            logging.debug(f'sending response from {self.service_name}.{handler_func.__name__} ...')

            MQ.send_rsp(self.service_name, handler_func.__name__, req_id, json.dumps(rsp))


class MessageQueue(object):
    """
    Message Queue class.
    """

    def __init__(self):
        host, port = os.getenv('MESSAGE_QUEUE_HOSTNAME', 'localhost'), os.getenv('MESSAGE_QUEUE_PORTNR', '50051')
        self.channel = grpc.insecure_channel('{}:{}'.format(host, port))
        self.stub = MessageQueueStub(self.channel)
        self.subscribers = {}

    def __del__(self):
        self.channel.close()

    def send_req(self, service_name, func_name, payload, req_id=None):
        """
        Send a request.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param payload: The request payload.
        :param req_id: The ID of the request, may be None.
        :return: The ID of the sent request.
        """
        response = self.stub.send_req(
            SendRequest(
                service_name=service_name,
                func_name=func_name,
                req_id=req_id or str(uuid.uuid4()),
                payload=payload,
            ))

        return response.req_id

    def recv_rsp(self, service_name, func_name, req_id, timeout=0):
        """
        Receive a response.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param req_id: The ID of the request, may be None.
        :param timeout: The timeout in seconds.
        :return: The payload of the response.
        """
        response = self.stub.recv_rsp(
            ReceiveRequest(
                service_name=service_name,
                func_name=func_name,
                req_id=req_id,
                timeout=timeout
            ))

        return response.payload

    def get_rsp(self, service_name, func_name, req_id):
        """
        Get a response.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param req_id: The ID of the request, may be None.
        :return: The payload of the response.
        """
        response = self.stub.get_rsp(
            GetRequest(
                service_name=service_name,
                func_name=func_name,
                req_id=req_id,
            ))

        return response.payload

    def ack_rsp(self, service_name, func_name, req_id, payload):
        """
        Acknowledge a response.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param req_id: The ID of the request.
        :param payload: The payload of the response.
        :return: Success.
        """
        response = self.stub.ack_rsp(
            AcknowledgeRequest(
                service_name=service_name,
                func_name=func_name,
                req_id=req_id,
                payload=payload,
            ))

        return response.success

    def recv_req(self, service_name, func_name, timeout=0):
        """
        Receive a request.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param timeout: The timeout in seconds.
        :return: The payload of the request.
        """
        request = self.stub.recv_req(
            ReceiveRequest(
                service_name=service_name,
                func_name=func_name,
                req_id=None,
                timeout=timeout
            ))

        return request.req_id, request.payload

    def get_req(self, service_name, func_name):
        """
        Get a request.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :return: The payload of the response.
        """
        request = self.stub.get_req(
            GetRequest(
                service_name=service_name,
                func_name=func_name,
                req_id=None,
            ))

        return request.req_id, request.payload

    def ack_req(self, service_name, func_name, req_id):
        """
        Acknowledge a request.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param req_id: The ID of the request.
        :return: The payload of the response.
        """
        request = self.stub.ack_req(
            AcknowledgeRequest(
                service_name=service_name,
                func_name=func_name,
                req_id=req_id,
                payload=None,
            ))

        return request.success

    def send_rsp(self, service_name, func_name, req_id, payload):
        """
        Send a response.

        :param service_name: The remote service to call.
        :param func_name: The remote function to call.
        :param req_id: The ID of the request.
        :param payload: The payload of the response.
        :return: Success.
        """
        response = self.stub.send_rsp(
            SendRequest(
                service_name=service_name,
                func_name=func_name,
                req_id=req_id,
                payload=payload,
            ))

        return response.req_id


MQ = MessageQueue()
