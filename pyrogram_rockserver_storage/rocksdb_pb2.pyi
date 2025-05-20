from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ColumnHashType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    XXHASH32: _ClassVar[ColumnHashType]
    XXHASH8: _ClassVar[ColumnHashType]
    ALLSAME8: _ClassVar[ColumnHashType]
    FIXEDINTEGER32: _ClassVar[ColumnHashType]

class Operation(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    NOTHING: _ClassVar[Operation]
    PREVIOUS: _ClassVar[Operation]
    CURRENT: _ClassVar[Operation]
    FOR_UPDATE: _ClassVar[Operation]
    EXISTS: _ClassVar[Operation]
    DELTA: _ClassVar[Operation]
    MULTI: _ClassVar[Operation]
    CHANGED: _ClassVar[Operation]
    PREVIOUS_PRESENCE: _ClassVar[Operation]

class PutBatchMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WRITE_BATCH: _ClassVar[PutBatchMode]
    WRITE_BATCH_NO_WAL: _ClassVar[PutBatchMode]
    SST_INGESTION: _ClassVar[PutBatchMode]
    SST_INGEST_BEHIND: _ClassVar[PutBatchMode]
XXHASH32: ColumnHashType
XXHASH8: ColumnHashType
ALLSAME8: ColumnHashType
FIXEDINTEGER32: ColumnHashType
NOTHING: Operation
PREVIOUS: Operation
CURRENT: Operation
FOR_UPDATE: Operation
EXISTS: Operation
DELTA: Operation
MULTI: Operation
CHANGED: Operation
PREVIOUS_PRESENCE: Operation
WRITE_BATCH: PutBatchMode
WRITE_BATCH_NO_WAL: PutBatchMode
SST_INGESTION: PutBatchMode
SST_INGEST_BEHIND: PutBatchMode

class ColumnSchema(_message.Message):
    __slots__ = ("fixedKeys", "variableTailKeys", "hasValue")
    FIXEDKEYS_FIELD_NUMBER: _ClassVar[int]
    VARIABLETAILKEYS_FIELD_NUMBER: _ClassVar[int]
    HASVALUE_FIELD_NUMBER: _ClassVar[int]
    fixedKeys: _containers.RepeatedScalarFieldContainer[int]
    variableTailKeys: _containers.RepeatedScalarFieldContainer[ColumnHashType]
    hasValue: bool
    def __init__(self, fixedKeys: _Optional[_Iterable[int]] = ..., variableTailKeys: _Optional[_Iterable[_Union[ColumnHashType, str]]] = ..., hasValue: bool = ...) -> None: ...

class Delta(_message.Message):
    __slots__ = ("previous", "current")
    PREVIOUS_FIELD_NUMBER: _ClassVar[int]
    CURRENT_FIELD_NUMBER: _ClassVar[int]
    previous: bytes
    current: bytes
    def __init__(self, previous: _Optional[bytes] = ..., current: _Optional[bytes] = ...) -> None: ...

class Previous(_message.Message):
    __slots__ = ("previous",)
    PREVIOUS_FIELD_NUMBER: _ClassVar[int]
    previous: bytes
    def __init__(self, previous: _Optional[bytes] = ...) -> None: ...

class Changed(_message.Message):
    __slots__ = ("changed",)
    CHANGED_FIELD_NUMBER: _ClassVar[int]
    changed: bool
    def __init__(self, changed: bool = ...) -> None: ...

class PreviousPresence(_message.Message):
    __slots__ = ("present",)
    PRESENT_FIELD_NUMBER: _ClassVar[int]
    present: bool
    def __init__(self, present: bool = ...) -> None: ...

class UpdateBegin(_message.Message):
    __slots__ = ("previous", "updateId")
    PREVIOUS_FIELD_NUMBER: _ClassVar[int]
    UPDATEID_FIELD_NUMBER: _ClassVar[int]
    previous: bytes
    updateId: int
    def __init__(self, previous: _Optional[bytes] = ..., updateId: _Optional[int] = ...) -> None: ...

class KV(_message.Message):
    __slots__ = ("keys", "value")
    KEYS_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    keys: _containers.RepeatedScalarFieldContainer[bytes]
    value: bytes
    def __init__(self, keys: _Optional[_Iterable[bytes]] = ..., value: _Optional[bytes] = ...) -> None: ...

class KVBatch(_message.Message):
    __slots__ = ("entries",)
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    entries: _containers.RepeatedCompositeFieldContainer[KV]
    def __init__(self, entries: _Optional[_Iterable[_Union[KV, _Mapping]]] = ...) -> None: ...

class OpenTransactionRequest(_message.Message):
    __slots__ = ("timeoutMs",)
    TIMEOUTMS_FIELD_NUMBER: _ClassVar[int]
    timeoutMs: int
    def __init__(self, timeoutMs: _Optional[int] = ...) -> None: ...

class OpenTransactionResponse(_message.Message):
    __slots__ = ("transactionId",)
    TRANSACTIONID_FIELD_NUMBER: _ClassVar[int]
    transactionId: int
    def __init__(self, transactionId: _Optional[int] = ...) -> None: ...

class CloseTransactionRequest(_message.Message):
    __slots__ = ("transactionId", "timeoutMs", "commit")
    TRANSACTIONID_FIELD_NUMBER: _ClassVar[int]
    TIMEOUTMS_FIELD_NUMBER: _ClassVar[int]
    COMMIT_FIELD_NUMBER: _ClassVar[int]
    transactionId: int
    timeoutMs: int
    commit: bool
    def __init__(self, transactionId: _Optional[int] = ..., timeoutMs: _Optional[int] = ..., commit: bool = ...) -> None: ...

class CloseTransactionResponse(_message.Message):
    __slots__ = ("successful",)
    SUCCESSFUL_FIELD_NUMBER: _ClassVar[int]
    successful: bool
    def __init__(self, successful: bool = ...) -> None: ...

class CloseFailedUpdateRequest(_message.Message):
    __slots__ = ("updateId",)
    UPDATEID_FIELD_NUMBER: _ClassVar[int]
    updateId: int
    def __init__(self, updateId: _Optional[int] = ...) -> None: ...

class CreateColumnRequest(_message.Message):
    __slots__ = ("name", "schema")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SCHEMA_FIELD_NUMBER: _ClassVar[int]
    name: str
    schema: ColumnSchema
    def __init__(self, name: _Optional[str] = ..., schema: _Optional[_Union[ColumnSchema, _Mapping]] = ...) -> None: ...

class CreateColumnResponse(_message.Message):
    __slots__ = ("columnId",)
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    columnId: int
    def __init__(self, columnId: _Optional[int] = ...) -> None: ...

class DeleteColumnRequest(_message.Message):
    __slots__ = ("columnId",)
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    columnId: int
    def __init__(self, columnId: _Optional[int] = ...) -> None: ...

class GetColumnIdRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class GetColumnIdResponse(_message.Message):
    __slots__ = ("columnId",)
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    columnId: int
    def __init__(self, columnId: _Optional[int] = ...) -> None: ...

class PutRequest(_message.Message):
    __slots__ = ("transactionOrUpdateId", "columnId", "data")
    TRANSACTIONORUPDATEID_FIELD_NUMBER: _ClassVar[int]
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    transactionOrUpdateId: int
    columnId: int
    data: KV
    def __init__(self, transactionOrUpdateId: _Optional[int] = ..., columnId: _Optional[int] = ..., data: _Optional[_Union[KV, _Mapping]] = ...) -> None: ...

class PutBatchInitialRequest(_message.Message):
    __slots__ = ("columnId", "mode")
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    columnId: int
    mode: PutBatchMode
    def __init__(self, columnId: _Optional[int] = ..., mode: _Optional[_Union[PutBatchMode, str]] = ...) -> None: ...

class PutBatchRequest(_message.Message):
    __slots__ = ("initialRequest", "data")
    INITIALREQUEST_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    initialRequest: PutBatchInitialRequest
    data: KVBatch
    def __init__(self, initialRequest: _Optional[_Union[PutBatchInitialRequest, _Mapping]] = ..., data: _Optional[_Union[KVBatch, _Mapping]] = ...) -> None: ...

class PutMultiInitialRequest(_message.Message):
    __slots__ = ("transactionOrUpdateId", "columnId")
    TRANSACTIONORUPDATEID_FIELD_NUMBER: _ClassVar[int]
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    transactionOrUpdateId: int
    columnId: int
    def __init__(self, transactionOrUpdateId: _Optional[int] = ..., columnId: _Optional[int] = ...) -> None: ...

class PutMultiRequest(_message.Message):
    __slots__ = ("initialRequest", "data")
    INITIALREQUEST_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    initialRequest: PutMultiInitialRequest
    data: KV
    def __init__(self, initialRequest: _Optional[_Union[PutMultiInitialRequest, _Mapping]] = ..., data: _Optional[_Union[KV, _Mapping]] = ...) -> None: ...

class PutMultiListRequest(_message.Message):
    __slots__ = ("initialRequest", "data")
    INITIALREQUEST_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    initialRequest: PutMultiInitialRequest
    data: _containers.RepeatedCompositeFieldContainer[KV]
    def __init__(self, initialRequest: _Optional[_Union[PutMultiInitialRequest, _Mapping]] = ..., data: _Optional[_Iterable[_Union[KV, _Mapping]]] = ...) -> None: ...

class GetRequest(_message.Message):
    __slots__ = ("transactionOrUpdateId", "columnId", "keys")
    TRANSACTIONORUPDATEID_FIELD_NUMBER: _ClassVar[int]
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    KEYS_FIELD_NUMBER: _ClassVar[int]
    transactionOrUpdateId: int
    columnId: int
    keys: _containers.RepeatedScalarFieldContainer[bytes]
    def __init__(self, transactionOrUpdateId: _Optional[int] = ..., columnId: _Optional[int] = ..., keys: _Optional[_Iterable[bytes]] = ...) -> None: ...

class GetResponse(_message.Message):
    __slots__ = ("value",)
    VALUE_FIELD_NUMBER: _ClassVar[int]
    value: bytes
    def __init__(self, value: _Optional[bytes] = ...) -> None: ...

class OpenIteratorRequest(_message.Message):
    __slots__ = ("transactionId", "columnId", "startKeysInclusive", "endKeysExclusive", "reverse", "timeoutMs")
    TRANSACTIONID_FIELD_NUMBER: _ClassVar[int]
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    STARTKEYSINCLUSIVE_FIELD_NUMBER: _ClassVar[int]
    ENDKEYSEXCLUSIVE_FIELD_NUMBER: _ClassVar[int]
    REVERSE_FIELD_NUMBER: _ClassVar[int]
    TIMEOUTMS_FIELD_NUMBER: _ClassVar[int]
    transactionId: int
    columnId: int
    startKeysInclusive: _containers.RepeatedScalarFieldContainer[bytes]
    endKeysExclusive: _containers.RepeatedScalarFieldContainer[bytes]
    reverse: bool
    timeoutMs: int
    def __init__(self, transactionId: _Optional[int] = ..., columnId: _Optional[int] = ..., startKeysInclusive: _Optional[_Iterable[bytes]] = ..., endKeysExclusive: _Optional[_Iterable[bytes]] = ..., reverse: bool = ..., timeoutMs: _Optional[int] = ...) -> None: ...

class OpenIteratorResponse(_message.Message):
    __slots__ = ("iteratorId",)
    ITERATORID_FIELD_NUMBER: _ClassVar[int]
    iteratorId: int
    def __init__(self, iteratorId: _Optional[int] = ...) -> None: ...

class CloseIteratorRequest(_message.Message):
    __slots__ = ("iteratorId",)
    ITERATORID_FIELD_NUMBER: _ClassVar[int]
    iteratorId: int
    def __init__(self, iteratorId: _Optional[int] = ...) -> None: ...

class SeekToRequest(_message.Message):
    __slots__ = ("iterationId", "keys")
    ITERATIONID_FIELD_NUMBER: _ClassVar[int]
    KEYS_FIELD_NUMBER: _ClassVar[int]
    iterationId: int
    keys: _containers.RepeatedScalarFieldContainer[bytes]
    def __init__(self, iterationId: _Optional[int] = ..., keys: _Optional[_Iterable[bytes]] = ...) -> None: ...

class SubsequentRequest(_message.Message):
    __slots__ = ("iterationId", "skipCount", "takeCount")
    ITERATIONID_FIELD_NUMBER: _ClassVar[int]
    SKIPCOUNT_FIELD_NUMBER: _ClassVar[int]
    TAKECOUNT_FIELD_NUMBER: _ClassVar[int]
    iterationId: int
    skipCount: int
    takeCount: int
    def __init__(self, iterationId: _Optional[int] = ..., skipCount: _Optional[int] = ..., takeCount: _Optional[int] = ...) -> None: ...

class GetRangeRequest(_message.Message):
    __slots__ = ("transactionId", "columnId", "startKeysInclusive", "endKeysExclusive", "reverse", "timeoutMs")
    TRANSACTIONID_FIELD_NUMBER: _ClassVar[int]
    COLUMNID_FIELD_NUMBER: _ClassVar[int]
    STARTKEYSINCLUSIVE_FIELD_NUMBER: _ClassVar[int]
    ENDKEYSEXCLUSIVE_FIELD_NUMBER: _ClassVar[int]
    REVERSE_FIELD_NUMBER: _ClassVar[int]
    TIMEOUTMS_FIELD_NUMBER: _ClassVar[int]
    transactionId: int
    columnId: int
    startKeysInclusive: _containers.RepeatedScalarFieldContainer[bytes]
    endKeysExclusive: _containers.RepeatedScalarFieldContainer[bytes]
    reverse: bool
    timeoutMs: int
    def __init__(self, transactionId: _Optional[int] = ..., columnId: _Optional[int] = ..., startKeysInclusive: _Optional[_Iterable[bytes]] = ..., endKeysExclusive: _Optional[_Iterable[bytes]] = ..., reverse: bool = ..., timeoutMs: _Optional[int] = ...) -> None: ...

class FirstAndLast(_message.Message):
    __slots__ = ("first", "last")
    FIRST_FIELD_NUMBER: _ClassVar[int]
    LAST_FIELD_NUMBER: _ClassVar[int]
    first: KV
    last: KV
    def __init__(self, first: _Optional[_Union[KV, _Mapping]] = ..., last: _Optional[_Union[KV, _Mapping]] = ...) -> None: ...

class EntriesCount(_message.Message):
    __slots__ = ("count",)
    COUNT_FIELD_NUMBER: _ClassVar[int]
    count: int
    def __init__(self, count: _Optional[int] = ...) -> None: ...
