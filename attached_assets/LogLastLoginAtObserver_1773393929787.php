<?php
namespace Bss\CustomerLoginLogs\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Bss\CustomerLoginLogs\Model\Logger;
use Magento\Customer\Api\CustomerRepositoryInterface;

class LogLastLoginAtObserver implements ObserverInterface
{
    /**
     * @var Logger
     */
    protected $bssLogger;

    /**
     * @var CustomerRepositoryInterface
     */
    protected $customerRepository;

    /**
     * @var \Magento\Framework\App\RequestInterface
     */
    protected $request;

    /**
     * @param Logger $bssLogger
     * @param CustomerRepositoryInterface $customerRepository
     */
    public function __construct(
        Logger $bssLogger,
        CustomerRepositoryInterface $customerRepository,
        \Magento\Framework\App\RequestInterface $request
    ) {
        $this->bssLogger = $bssLogger;
        $this->customerRepository = $customerRepository;
        $this->request = $request;
    }

    /**
     * @param Observer $observer
     * @return void
     * @throws \Magento\Framework\Exception\LocalizedException
     * @throws \Magento\Framework\Exception\NoSuchEntityException
     */
    public function execute(Observer $observer)
    {
        $params = $this->request->getParams();
        if ((isset($params['customers']) && $params['customers'] == 'login') || isset($params['login'])) {
            $customer = $observer->getEvent()->getCustomer();
            $customerId = $customer->getId();
            $customerById = $this->customerRepository->getById($customerId);
            $ps365Code = $customerById->getCustomAttribute('powersoft_code') ? $customerById->getCustomAttribute('powersoft_code')->getValue() : '';
            $customerFirstName = $customer->getData('firstname');
            $customerLastName = $customer->getData('lastname');
            $customerEmail = $customer->getEmail();
            $lastLogin = (new \DateTime())->format(\Magento\Framework\Stdlib\DateTime::DATETIME_PHP_FORMAT);
            $data = [
                'customer_id' => $customerId,
                'email' => $customerEmail,
                'first_name' => $customerFirstName,
                'last_name' => $customerLastName,
                'ps365_code' => $ps365Code,
                'last_login_at' => $lastLogin
            ];
            $this->bssLogger->logLoginInfo($data);
        }
    }
}
