import asyncio
import logging

from thriftpy2.contrib.aio.client import TAsyncClient
from thriftpy2.thrift import TApplicationException, TMessageType, args_to_kwargs

log = logging.getLogger(__name__)


class TParallelAsyncClient(TAsyncClient):
    def __init__(self, service, iprot, oprot=None):
        super().__init__(service, iprot, oprot)
        self._open_requests: dict[int, asyncio.Future] = {}
        self._message_processor: asyncio.Task | None = None

    async def _req(self, _api, *args, **kwargs):
        try:
            service_args = getattr(self._service, _api + "_args")
            kwargs = args_to_kwargs(service_args.thrift_spec, *args, **kwargs)
        except ValueError as e:
            raise TApplicationException(
                TApplicationException.UNKNOWN_METHOD,
                "missing required argument {arg} for {service}.{api}".format(
                    arg=e.args[0], service=self._service.__name__, api=_api
                ),
            )

        fut = await self._send(_api, **kwargs)
        if fut is not None:
            self._process_messages()
            return await fut

    async def _send(self, _api, **kwargs) -> asyncio.Future | None:
        oneway = getattr(getattr(self._service, _api + "_result"), "oneway")
        msg_type = TMessageType.ONEWAY if oneway else TMessageType.CALL
        seq_id = self._get_seqid()
        self._oprot.write_message_begin(_api, msg_type, seq_id)
        args = getattr(self._service, _api + "_args")()
        for k, v in kwargs.items():
            setattr(args, k, v)
        self._oprot.write_struct(args)
        self._oprot.write_message_end()
        await self._oprot.trans.flush()
        log.debug("Sent seqid %d: %s", seq_id, _api)

        if oneway:
            return None
        else:
            self._open_requests[seq_id] = asyncio.Future()
            return self._open_requests[seq_id]

    def _process_messages(self):
        if self._message_processor is None or self._message_processor.done():
            self._message_processor = asyncio.create_task(self._message_handler())

    async def _message_handler(self):
        while self._open_requests:
            fname, mtype, rseqid = await self._iprot.read_message_begin()
            log.debug("Reply for seqid %d: %s %s", rseqid, fname, mtype)
            fut = self._open_requests.pop(rseqid, None)
            if fut is None:
                log.error("Received message with unknown seqid %d", rseqid)

            try:
                fut.set_result(await self._process_message(fname, mtype))
            except Exception as e:
                fut.set_exception(e)

    async def _process_message(self, fname, mtype):
        """process a single message"""
        if mtype == TMessageType.EXCEPTION:
            x = TApplicationException()
            await self._iprot.read_struct(x)
            await self._iprot.read_message_end()
            raise x
        result = getattr(self._service, fname + "_result")()
        await self._iprot.read_struct(result)
        await self._iprot.read_message_end()

        if hasattr(result, "success") and result.success is not None:
            return result.success

        # void api without throws
        if len(result.thrift_spec) == 0:
            return

        # check throws
        for k, v in result.__dict__.items():
            if k != "success" and v:
                raise v

        # no throws & not void api
        if hasattr(result, "success"):
            raise TApplicationException(TApplicationException.MISSING_RESULT)

    def _get_seqid(self) -> int:
        seq_id = self._seqid
        self._seqid += 1
        return seq_id

    def close(self):
        if self._message_processor is not None and not self._message_processor.done():
            self._message_processor.cancel()
            self._message_processor = None
        super().close()
