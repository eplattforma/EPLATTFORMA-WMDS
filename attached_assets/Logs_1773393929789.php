<?php
namespace Bss\CustomerLoginLogs\Model;

use Magento\Framework\Model\AbstractModel;

class Logs extends AbstractModel
{
    /**
     * Define resource model
     */
    protected function _construct()
    {
        $this->_init(\Bss\CustomerLoginLogs\Model\ResourceModel\Logs::class);
    }
}
