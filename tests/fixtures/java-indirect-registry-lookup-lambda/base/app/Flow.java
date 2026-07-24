interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
class Registry {
    void put(String key, Task task) { int marker = 1; }
    Task find(String key) { return null; }
}
class Flow {
    Registry reg = new Registry();
    void wire() { reg.put("pay", () -> new Vault().transfer()); }
    void sink(String key) { reg.find(key).exec(); }
}
