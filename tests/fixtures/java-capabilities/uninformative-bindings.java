class UninformativeBindings {
    void invoke(Object receiver) throws Exception {
        var local = receiver;
        receiver.invoke(this);
        local.invoke(this);
    }
}
