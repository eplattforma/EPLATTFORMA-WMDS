<?php
namespace Bss\CustomerLoginLogs\Model\Api;

use Magento\Framework\Exception\LocalizedException;
class Activitylog extends \Simi\Simiconnector\Model\Api\Apiabstract
{
    /**
     * @return void
     */
    public function setBuilderQuery()
    {
        $logsCollection = $this->simiObjectManager->get('Bss\CustomerLoginLogs\Model\Logs')
            ->getCollection();
        $this->builderQuery = $logsCollection;
    }

    public function index()
    {
        return $this->show();
    }

    /**
     * @return mixed
     * @throws LocalizedException
     */
    public function store()
    {
        $data = $this->getData();
        $parameters = null;
        if (isset($data['contents'])) {
            $parameters = (array)$data['contents'];
        }
        if (isset($parameters['customer_id']) && isset($parameters['status'])) {
            $status = $parameters['status'];
            $customerId = $parameters['customer_id'];
            if ($status == 'closed') {
                $lastLogout = (new \DateTime())->format(\Magento\Framework\Stdlib\DateTime::DATETIME_PHP_FORMAT);
                $dataUpdate = [
                    'customer_id' => $customerId,
                    'last_logout_at' => $lastLogout
                ];
                $this->simiObjectManager->get('Bss\CustomerLoginLogs\Model\Logger')->logLogoutInfo($dataUpdate);
            } elseif ($status == 'opened') {
                try {
                    $customerById = $this->simiObjectManager->get('Magento\Customer\Api\CustomerRepositoryInterface')->getById($customerId);
                    $ps365Code = $customerById->getCustomAttribute('powersoft_code') ? $customerById->getCustomAttribute('powersoft_code')->getValue() : '';
                    $customerFirstName = $customerById->getFirstName();
                    $customerLastName = $customerById->getLastName();
                    $customerEmail = $customerById->getEmail();
                    $lastLogin = (new \DateTime())->format(\Magento\Framework\Stdlib\DateTime::DATETIME_PHP_FORMAT);
                    $dataUpdate = [
                        'customer_id' => $customerId,
                        'email' => $customerEmail,
                        'first_name' => $customerFirstName,
                        'last_name' => $customerLastName,
                        'ps365_code' => $ps365Code,
                        'last_login_at' => $lastLogin
                    ];
                    $this->simiObjectManager->get('Bss\CustomerLoginLogs\Model\Logger')->logLoginInfo($dataUpdate);
                } catch (\Exception $exception) {
                    throw new LocalizedException(__("Customer does not exists."));
                }
            }
        }
        return [];
    }
}