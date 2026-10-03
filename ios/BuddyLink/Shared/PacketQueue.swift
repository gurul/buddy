// FIFO with amortized constant-time removal. A BLE reply can contain thousands of packets;
// Array.removeFirst() would move the whole remaining reply for each write.
struct PacketQueue<Element> {
    private var storage: [Element?] = []
    private var head = 0

    var isEmpty: Bool { head == storage.count }
    var count: Int { storage.count - head }

    mutating func append(_ value: Element) { storage.append(value) }

    mutating func popFirst() -> Element? {
        guard !isEmpty else { return nil }
        let value = storage[head]
        storage[head] = nil                 // release sent packets even while later ones wait
        head += 1
        if isEmpty {
            removeAll()
        } else if head >= 1024, head >= storage.count - head {
            storage.removeFirst(head)       // compact only after at least half has drained
            head = 0
        }
        return value
    }

    mutating func removeAll() {
        storage.removeAll(keepingCapacity: true)
        head = 0
    }

    mutating func removeAll(where predicate: (Element) -> Bool) {
        storage = storage[head...].compactMap { value in
            guard let value, !predicate(value) else { return nil }
            return value
        }
        head = 0
    }

    func count(where predicate: (Element) -> Bool) -> Int {
        storage[head...].reduce(0) { count, value in
            count + ((value.map(predicate) ?? false) ? 1 : 0)
        }
    }
}
