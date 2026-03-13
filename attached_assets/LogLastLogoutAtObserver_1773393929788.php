<?php
namespace Bss\CustomerLoginLogs\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Bss\CustomerLoginLogs\Model\Logger;

class LogLastLogoutAtObserver implements ObserverInterface
{
    /**
     * @var Logger
     */
    protected $bssLogger;

    /**
     * @var \Magento\Framework\App\RequestInterface
     */
    protected $request;

    /**
     * @param Logger $bssLogger
     */
    public function __construct(
        Logger $bssLogger,
        \Magento\Framework\App\RequestInterface $request
    ) {
        $this->bssLogger = $bssLogger;
        $this->request = $request;
    }

    /**
     * @param Observer $observer
     * @return void
     */
    public function execute(Observer $observer)
    {
        $params = $this->request->getParams();
        $action = $this->request->getActionName();
        if ((isset($params['customers']) && $params['customers'] == 'logout') || $action == 'logout') {
            $customer = $observer->getEvent()->getCustomer();
            $customerId = $customer->getId();
            $lastLogout = (new \DateTime())->format(\Magento\Framework\Stdlib\DateTime::DATETIME_PHP_FORMAT);
            $data = [
                'customer_id' => $customerId,
                'last_logout_at' => $lastLogout
            ];
            $this->bssLogger->logLogoutInfo($data);
        }
    }
}
