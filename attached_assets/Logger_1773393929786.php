<?php
namespace Bss\CustomerLoginLogs\Model;

use Magento\Framework\App\ResourceConnection;

class Logger
{
    const TABLE_NAME = 'bss_customer_login_logs';

    /**
     * @var ResourceConnection
     */
    protected $resource;

    /**
     * @param ResourceConnection $resource
     */
    public function __construct(
        ResourceConnection $resource
    ) {
        $this->resource = $resource;
    }

    /**
     * @param $data
     * @return void
     */
    public function logLoginInfo($data)
    {
        $data = array_filter($data);

        if (!$data) {
            throw new \InvalidArgumentException("Login data is empty");
        }

        $connection = $this->resource->getConnection(ResourceConnection::DEFAULT_CONNECTION);
        $tableName = $this->resource->getTableName(self::TABLE_NAME);
        $connection->insert($tableName, $data);
    }

    /**
     * @param $data
     * @return void
     */
    public function logLogoutInfo($data)
    {
        $data = array_filter($data);

        if (!$data) {
            throw new \InvalidArgumentException("Logout data is empty");
        }
        $connection = $this->resource->getConnection(ResourceConnection::DEFAULT_CONNECTION);
        $tableName = $this->resource->getTableName(self::TABLE_NAME);

        if (isset($data['customer_id'])) {
            $customerId = $data['customer_id'];
            $logIdsLastLogin = $this->getLogIdsLastLogin($customerId);
            if (isset(end($logIdsLastLogin)['log_id'])) {
                $lastLogIdForCus = end($logIdsLastLogin)['log_id'];
                $bind = ['last_logout_at' => $data['last_logout_at']];
                $connection->update(
                    $tableName,
                    $bind,
                    ['log_id=?' => (int)$lastLogIdForCus]
                );
            }
        }

    }

    /**
     * @param $customerId
     * @return array
     */
    public function getLogIdsLastLogin($customerId)
    {
        $connection = $this->resource->getConnection(ResourceConnection::DEFAULT_CONNECTION);
        $tableName = $this->resource->getTableName(self::TABLE_NAME);
        $select = $connection->select()
            ->from(
                ['l' => $tableName],
                ['log_id']
            )->where(
                'customer_id=?', $customerId
            );
        return $connection->fetchAll($select);
    }
}
