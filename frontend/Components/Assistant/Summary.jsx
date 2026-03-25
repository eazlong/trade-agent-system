import React, { useState, useEffect } from "react";
import { useToasts } from "react-toast-notifications";
import { getSummaries, deleteSummary, getPlan } from "services/assistant.service";
import {
  Table,
  Button,
  ActionIcon,
  Modal,
  Text,
  Group,
  Accordion,
  Loader,
  Tabs,
  Paper
} from "@mantine/core";
import { DataTable } from 'mantine-datatable';
import { useMediaQuery } from '@mantine/hooks';
import { IconTrash, IconChevronUp, IconChevronDown } from "@tabler/icons-react";
import Link from "next/link";
import Editor from "Components/Assistant/Editor";
import ShareContent from "Components/Assistant/ShareContent";
import TradingPlanModal from "Components/Assistant/TradingPlanModal";
import NewSummary from "Components/Assistant/NewSummary"; // Import NewSummary
import {side} from "Utils/order";

const orderColumns = [
  { accessor: 'timestamp', title: '开/平仓时间', textAlignment: 'center', render: (order) => new Date(order.timestamp).toLocaleString() },
  { accessor: 'symbol', title: 'Symbol', textAlignment: 'center' },
  { accessor: 'side', title: '方向', textAlignment: 'center', render: (order) => side(order) },
  { accessor: 'position', title: '仓位', textAlignment: 'center', render: (order) => `${order.amount}(≈${parseFloat(order.amount * order.price).toFixed(2)}USDT）` },
  { accessor: 'price', title: '价位', textAlignment: 'center', render: (order) => `${order.price} USDT` },
  { accessor: 'profit', title: '盈利', textAlignment: 'center', render: (order) => `${parseFloat(order.profit).toFixed(2)} USDT` },
];

const Summary = ({ who = null }) => {
  const [summary, setSummary] = useState([]);
  const [expandedItems, setExpandedItems] = useState({});
  const [openPlanModal, setOpenPlanModal] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState("");
  const [userPlanContent, setUserPlanContent] = useState("");
  const [userCheckCondition, setUserCheckCondition] = useState("");
  const [selectedDate, setSelectedDate] = useState(null);
  const [deleteSummaryId, setDeleteSummaryId] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const isMobile = useMediaQuery('(max-width: 768px)');
  const [selectedOrder, setSelectedOrder] = useState(null);
  const [activeTab, setActiveTab] = useState('my-summary');

  const { addToast } = useToasts();
  const showToast = (message, appearance) => {
    addToast(message, { appearance });
  };

  const handleClosePlanModal = () => {
    setOpenPlanModal(false);
  };

  const handleOpenDeleteDialog = (id, e) => {
    if (e) e.stopPropagation();
    setDeleteSummaryId(id);
    setDeleteDialogOpen(true);
  };

  const handleCloseDeleteDialog = () => {
    setDeleteDialogOpen(false);
    setDeleteSummaryId(null);
  };

  const handleConfirmDelete = () => {
    if (!deleteSummaryId) return;
    deleteSummary(deleteSummaryId)
      .then(() => {
        setSummary(summary.filter((p) => p.id !== deleteSummaryId));
        showToast("删除成功", "success");
        setDeleteSummaryId(null);
      })
      .catch((err) => {
        showToast("删除失败:" + err.toString(), "error");
      })
      .finally(() => {
        handleCloseDeleteDialog();
      });
  };

  const toggleExpand = (itemId) => {
    setExpandedItems((prev) => ({
      ...prev,
      [itemId]: !prev[itemId],
    }));
  };

  const handleOpenPlan = async (order, e) => {
    e.stopPropagation();
    try {
      const date = order[0].timestamp.split("T")[0];
      const response = await getPlan(order[0].symbol, date);
      if (response.status === 200) {
        setSelectedSymbol(order[0].symbol);
        setUserPlanContent(response.data.content);
        setUserCheckCondition(response.data.condition);
        setSelectedDate(date);
        setOpenPlanModal(true);
      }
    } catch (error) {
      showToast("获取交易计划失败:" + error.toString(), "error");
    }
  };

  const handleOpenKline = (order, e) => {
    if (e) e.stopPropagation(); 
    order[order.length - 1].parent_order = order;
    setSelectedOrder(order[order.length - 1]);
    setActiveTab('new-summary');
  };

  useEffect(() => {
    const fetchSummary = async () => {
      setLoading(true);
      try {
        const response = await getSummaries(who);
        setSummary(response.data);
        const initialExpandState = {};
        response.data.forEach((item, index) => {
          initialExpandState[item.id] = index === 0;
        });
        setExpandedItems(initialExpandState);
      } catch (error) {
        if (error.response && error.response.status === 404) {
          showToast("暂无数据", "info");
        } else {
          showToast("获取复盘总结失败:" + error.toString(), "error");
        }
      } finally {
        setLoading(false);
      }
    };

    fetchSummary();
  }, [who]);
  
  const columns = [
    {
      accessor: 'symbol',
      title: 'Symbol',
      textAlignment: 'center',
      render: (item) => item.orders[0]?.symbol || "N/A",
    },
    {
      accessor: 'timestamp',
      title: '开单时间',
      textAlignment: 'center',
      render: (item) => item.orders[0]?.timestamp ? new Date(item.orders[0]?.timestamp).toLocaleString() : "N/A",
    },
    {
      accessor: 'orderCount',
      title: '订单数',
      textAlignment: 'center',
      render: (item) => item.orders.length,
    },
    {
      accessor: 'actions',
      title: '操作',
      textAlignment: 'center',
      render: (item) => (
        <Group position="center">
          <Button component="a" size="xs" variant="outline" onClick={(e) => handleOpenKline(item.orders, e)}>查看K线</Button>
          <Button size="xs" variant="outline" onClick={(e) => handleOpenPlan(item.orders, e)}>查看对应交易计划</Button>
          <ShareContent
            contentType="summary"
            contentId={item.id}
            title={`${item.orders[0]?.symbol || ""} ${item.orders[0]?.timestamp?.split("T")[0] || ""} 交易总结`}
          />
          <ActionIcon variant="transparent" size="lg" onClick={(e) => handleOpenDeleteDialog(item.id, e)}>
            <IconTrash color="red" size={18} />
          </ActionIcon>
        </Group>
      ),
    },
  ];

  const renderDesktopView = () => (
    <DataTable
      highlightOnHover
      records={summary}
      columns={columns}
      noRecordsText=""
      noRecordsIcon={<></>}
      rowExpansion={{
        content: ({ record }) => (
          <Paper p="md" mt="sm">
              <DataTable
                records={record.orders}
                columns={orderColumns}
                striped
                highlightOnHover
                mb="sm"
                noRecordsText=""
                noRecordsIcon={<></>}
              />
              <Editor contentData={record.content} />
          </Paper>
        ),
      }}
    />
  );

  const renderMobileView = () => (
    <Accordion>
      {summary.map((item) => (
        <Accordion.Item key={item.id} value={item.id.toString()}>
          <Accordion.Control>
            <Group position="apart">
              <Text weight={500}>{item.orders[0]?.symbol || "交易总结"} - {item.orders[0]?.timestamp?.split("T")[0] || ""}</Text>
            </Group>
          </Accordion.Control>
          <Accordion.Panel>
            <Editor contentData={item.content} />
            <Group mt="md">
              <Button size="xs" variant="outline" onClick={() => handleOpenPlan(item.orders)}>查看计划</Button>
              <ShareContent
                contentType="summary"
                contentId={item.id}
                title={`${item.orders[0]?.symbol || ""} ${item.orders[0]?.timestamp?.split("T")[0] || ""} 交易总结`}
              />
              <ActionIcon color="red" size="sm" onClick={(e) => handleOpenDeleteDialog(item.id, e)}>
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
          </Accordion.Panel>
        </Accordion.Item>
      ))}
    </Accordion>
  );


  return (
    <div className="h-full w-full">
      <TradingPlanModal
        symbol={selectedSymbol}
        open={openPlanModal}
        onClose={handleClosePlanModal}
        userPlanContent={userPlanContent}
        userCheckCondition={userCheckCondition}
        date={selectedDate}
      />

      <Tabs value={activeTab} className="h-full w-full">
        <Tabs.List>
          <Tabs.Tab value="my-summary" onClick={() => {
            setActiveTab("my-summary");
            setSelectedOrder(null);
          }}>我的复盘</Tabs.Tab>
          <Tabs.Tab value="new-summary" onClick={() => {
            setActiveTab("new-summary");
            setSelectedOrder(null);
          }}>我要复盘</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="my-summary" pt="xs" className="h-full w-full">
            {loading ? (
                <Group position="center" mt="xl" className="h-full w-full">
                    <Loader />
                </Group>
            ) : summary.length > 0 ? (
                <div style={{ overflow: "auto" }}>
                  {isMobile ? renderMobileView() : renderDesktopView()}
                </div>
            ) : (
                <Group position="center" mt="xl">
                    <Text>暂无数据</Text>
                </Group>
            )}
        </Tabs.Panel>

        <Tabs.Panel value="new-summary" className="h-full w-full">
          <NewSummary initRecord={selectedOrder} />
        </Tabs.Panel>
      </Tabs>
      
      <Modal
        opened={deleteDialogOpen}
        onClose={handleCloseDeleteDialog}
        title="确认删除"
      >
        <Text>确定要删除该计划吗？此操作不可撤销。</Text>
        <Group position="right" mt="md">
          <Button variant="default" onClick={handleCloseDeleteDialog}>取消</Button>
          <Button color="red" onClick={handleConfirmDelete}>确认删除</Button>
        </Group>
      </Modal>
    </div>
  );
};

export default Summary;
