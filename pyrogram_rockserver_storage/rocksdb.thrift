namespace java it.cavallium.rockserver.core.common.api

struct ColumnSchema {
  1: list<i32> fixedKeys,
  2: list<ColumnHashType> variableTailKeys,
  3: bool hasValue
}

enum ColumnHashType {
  XXHASH32 = 1,
  XXHASH8 = 2,
  ALLSAME8 = 3
}

enum Operation {
  NOTHING = 1,
  PREVIOUS = 2,
  CURRENT = 3,
  FOR_UPDATE = 4,
  EXISTS = 5,
  DELTA = 6,
  MULTI = 7,
  CHANGED = 8,
  PREVIOUS_PRESENCE = 9
}

struct Delta {
  1: optional binary previous,
  2: optional binary current
}

struct OptionalBinary {
  1: optional binary value
}

struct UpdateBegin {
  1: optional binary previous,
  2: optional i64 updateId
}

service RocksDB {

   i64 openTransaction(1: required i64 timeoutMs),

   bool closeTransaction(1: required i64 transactionId, 2: required bool commit),

   void closeFailedUpdate(1: required i64 updateId),

   i64 createColumn(1: required string name, 2: required ColumnSchema schema),

   void deleteColumn(1: required i64 columnId),

   i64 getColumnId(1: required string name),

   oneway void putFast(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<binary> keys, 4: required binary value),

   void put(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<binary> keys, 4: required binary value),

   void putMulti(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<list<binary>> keysMulti, 4: required list<binary> valueMulti),

   OptionalBinary putGetPrevious(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<binary> keys, 4: required binary value),

   Delta putGetDelta(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<binary> keys, 4: required binary value),

   bool putGetChanged(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<binary> keys, 4: required binary value),

   bool putGetPreviousPresence(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<binary> keys, 4: required binary value),

   OptionalBinary get(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<binary> keys),

   UpdateBegin getForUpdate(1: required i64 transactionOrUpdateId, 2: required i64 columnId, 3: required list<binary> keys),

   bool exists(1: required i64 transactionOrUpdateId, 3: required i64 columnId, 4: required list<binary> keys),

   i64 openIterator(1: required i64 transactionId, 2: required i64 columnId, 3: required list<binary> startKeysInclusive, 4: list<binary> endKeysExclusive, 5: required bool reverse, 6: required i64 timeoutMs),

   void closeIterator(1: required i64 iteratorId),

   void seekTo(1: required i64 iterationId, 2: required list<binary> keys),

   void subsequent(1: required i64 iterationId, 2: required i64 skipCount, 3: required i64 takeCount),

   bool subsequentExists(1: required i64 iterationId, 2: required i64 skipCount, 3: required i64 takeCount),

   list<OptionalBinary> subsequentMultiGet(1: required i64 iterationId, 2: required i64 skipCount, 3: required i64 takeCount),

}
