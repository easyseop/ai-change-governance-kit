package app;

interface PaymentPort {
    void pay();

    default void audit() {}
}

interface StandalonePort {
    default void audit() {}

    static void utility() {}

    private void helper() {}

    default void delegate() {
        helper();
    }
}

interface BaseSettlementPort {
    void settle();

    default void audit() {}
}

interface SpecializedSettlementPort extends BaseSettlementPort {
    @Override
    default void audit() {}
}

class SettlementPayment implements SpecializedSettlementPort {
    public void settle() {}
}

abstract class AbstractSettlementPort {
    void settle() {}
}

class ConcreteSettlementPort extends AbstractSettlementPort {
    @Override
    void settle() {}
}

class CardPayment implements PaymentPort {
    public void pay() {}
}

class RegionalCardPayment extends CardPayment {
    public void pay() {}
}

class WirePayment implements PaymentPort {
    public void pay() {}
}

class BillingService {
    @Autowired
    PaymentPort injected;

    private final PaymentPort constructed;

    BillingService(PaymentPort constructed) {
        this.constructed = constructed;
    }

    @Transactional
    void billInjected() {
        injected.pay();
    }

    void billConstructed() {
        constructed.pay();
    }

    void auditInjected() {
        injected.audit();
    }

    void auditStandalone(StandalonePort standalone) {
        standalone.audit();
    }

    void useStandaloneUtility() {
        StandalonePort.utility();
    }

    void settleViaBaseInterface(BaseSettlementPort port) {
        port.settle();
    }

    void auditViaBaseInterface(BaseSettlementPort port) {
        port.audit();
    }

    void settleViaAbstractClass(AbstractSettlementPort port) {
        port.settle();
    }

    void settleViaConcreteClass(ConcreteSettlementPort port) {
        port.settle();
    }

    void reflect(Method method, Object target) throws Exception {
        method.invoke(target);
    }
}
