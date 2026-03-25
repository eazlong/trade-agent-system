import React, { useState, useEffect, useRef } from "react";
import {
  Box,
  Title,
  Button,
  Divider,
  Modal,
  Select,
  Loader,
  Text,
  Stack,
  Group,
  Paper,
  Container,
  Card,
  Badge,
  ActionIcon,
  Flex,
  Space,
  Tooltip
} from "@mantine/core";
import { modals } from '@mantine/modals';
import { IconChevronDown, IconChevronUp, IconPlus, IconAnalyze, IconTrash, IconShare } from '@tabler/icons-react';
import dayjs from "dayjs";
import Link from "next/link";
import Editor from "./Editor";
import ShareContent from "./ShareContent";
import {
  getAllPlan,
  createPlan,
  deletePlan,
} from "../../services/assistant.service";
import { analyzePlan } from "../../services/ai.service";

const TradingPlan = () => {
  const [plans, setPlans] = useState({});
  const [expandedPlans, setExpandedPlans] = useState({});
  const [expandedSymbolPlans, setExpandedSymbolPlans] = useState({});
  const [planType, setPlanType] = useState("today");

  const planTypeOptions = [
    { value: "today", label: "今日计划" },
    { value: "tomorrow", label: "明日计划" },
    { value: "this_week", label: "本周计划" },
    { value: "next_week", label: "下周计划" },
  ];

  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const scrollRef = useRef(null);

  const [loadingAnalysis, setLoadingAnalysis] = useState(false);
  const [analysisResult, setAnalysisResult] = useState("");
  const [openAnalysisDialog, setOpenAnalysisDialog] = useState(false);

  const currentYear = dayjs().year();

  // 获取计划列表
  const fetchPlans = (pageNum = 1, append = false) => {
    setLoadingMore(true);
    getAllPlan(null, pageNum)
      .then((res) => {
        const data = res.data.results;
        if (data.length > 0) {
          const plansByDate = data.reduce((acc, plan) => {
            const date = plan.date;
            if (!acc[date]) {
              acc[date] = [];
            }
            acc[date].push(plan);
            return acc;
          }, {});

          setPlans(append ? (prev) => ({ ...prev, ...plansByDate }) : plansByDate);
          
          // 初始化展开状态
          const initialExpandState = {};
          Object.keys(plansByDate).forEach((date, idx) => {
            initialExpandState[date] = idx === 0;
          });
          if (!append) {
            setExpandedPlans(initialExpandState);
          }
        }
        setHasMore(!!res.data.next);
        setPage(pageNum);
      })
      .catch((err) => {
        console.log(err);
      })
      .finally(() => {
        setLoadingMore(false);
      });
  };

  useEffect(() => {
    fetchPlans(1, false);
  }, []);

  // 添加滚动监听器用于无限加载
  useEffect(() => {
    const scrollContainer = scrollRef.current;
    if (!scrollContainer) return;

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
      const scrollPercentage = (scrollTop + clientHeight) / scrollHeight;
      
      // 当滚动到 85% 时开始加载下一页
      if (scrollPercentage > 0.85 && hasMore && !loadingMore) {
        fetchPlans(page + 1, true);
      }
    };

    scrollContainer.addEventListener('scroll', handleScroll);
    return () => {
      scrollContainer.removeEventListener('scroll', handleScroll);
    };
  }, [hasMore, loadingMore, page]);

  // 删除确认
  const handleDeletePlan = (id, date, e) => {
    e.stopPropagation();
    console.log(id, date);
    modals.openConfirmModal({
      title: '确认删除',
      children: <Text size="sm">确定要删除这个交易计划吗？此操作不可恢复。</Text>,
      labels: { confirm: '删除', cancel: '取消' },
      confirmProps: { color: 'red' },
      onConfirm: () => {
        deletePlan(id)
          .then(() => {
            setPlans((prev) => {
              const newPlans = { ...prev };
              newPlans[date] = newPlans[date].filter((p) => p.id !== id);
              if (newPlans[date].length === 0) {
                delete newPlans[date];
              }
              return newPlans;
            });
          })
          .catch((err) => console.log(err));
      },
    });
  };

  // 切换展开状态
  const toggleExpand = (date) => {
    setExpandedPlans(prev => ({ ...prev, [date]: !prev[date] }));
  };

  const toggleSymbolExpand = (id) => {
    setExpandedSymbolPlans(prev => ({ ...prev, [id]: !prev[id] }));
  };

  // AI分析
  const handleAnalyzePlan = (plan) => {
    if (!plan?.id) return;
    
    setLoadingAnalysis(true);
    setAnalysisResult("分析中...");
    setOpenAnalysisDialog(true);

    analyzePlan(plan.id)
      .then((res) => setAnalysisResult(res.data.message))
      .catch(() => setAnalysisResult("AI分析失败，请稍后再试。"))
      .finally(() => setLoadingAnalysis(false));
  };

  // 新建计划
  const handleNewPlan = () => {
    let date = dayjs();
    switch (planType) {
      case "tomorrow": date = date.add(1, 'day'); break;
      case "this_week": date = dayjs().startOf('week'); break;
      case "next_week": date = dayjs().startOf('week').add(1, 'week'); break;
    }

    createPlan("ALL", { date: date.format("YYYY-MM-DD"), type: planType })
      .then((res) => {
        const dateStr = date.format("YYYY-MM-DD");
        setPlans(prev => {
          const newPlans = { ...prev };
          
          // 如果这个日期已经存在，添加到该日期的计划列表中
          if (newPlans[dateStr]) {
            newPlans[dateStr] = [res.data, ...newPlans[dateStr]];
          } else {
            // 如果是新日期，需要按时间顺序插入
            newPlans[dateStr] = [res.data];
            
            // 重新排序所有日期键，确保时间顺序正确
            const sortedPlans = {};
            const sortedDates = Object.keys(newPlans).sort((a, b) => {
              return dayjs(b).valueOf() - dayjs(a).valueOf(); // 降序排列，最新的在前
            });
            
            sortedDates.forEach(date => {
              sortedPlans[date] = newPlans[date];
            });
            
            return sortedPlans;
          }
          
          return newPlans;
        });
        
        // 展开新创建的计划所在日期
        setExpandedPlans(prev => ({ ...prev, [dateStr]: true }));
      })
      .catch(console.log);
  };

  // 渲染计划卡片
  const renderPlanCard = (date, planList) => {
    const totalPlans = planList.filter(p => p.symbol === 'ALL');
    const symbolPlans = planList.filter(p => p.symbol !== 'ALL');

    return (
      <Stack spacing="lg">
        {/* 全局计划 */}
        {totalPlans.map(plan => (
          <Card key={plan.id} shadow="sm" padding="lg" radius="md" withBorder>
            <Card.Section withBorder inheritPadding py="xs">
              <Flex justify="space-between" align="center">
                <Badge size="sm" variant="light" color="grape">
                  {planTypeOptions.find(opt => opt.value === plan.type)?.label}
                </Badge>
                <Group spacing={8}>
                  <Tooltip label="分享计划" withArrow>
                    <Box onClick={(e) => { e.stopPropagation(); }}>
                      <ShareContent
                        contentType="plan"
                        contentId={plan.id}
                        title={`${plan.date} 交易计划`}
                        iconSize={18}
                        showText={false}
                      />
                    </Box>
                  </Tooltip>
                  {plan.content && (
                    <Tooltip label="AI智能分析" withArrow>
                      <ActionIcon
                        variant="subtle"
                        size="md"
                        color="blue"
                        loading={loadingAnalysis}
                        onClick={(e) => { e.stopPropagation(); handleAnalyzePlan(plan); }}
                      >
                        <IconAnalyze size={18} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                  <Tooltip label="删除计划" withArrow>
                    <ActionIcon
                      variant="subtle"
                      size="md"
                      color="red"
                      onClick={(e) => handleDeletePlan(plan.id, date, e)}
                    >
                      <IconTrash size={18} />
                    </ActionIcon>
                  </Tooltip>
                </Group>
              </Flex>
            </Card.Section>
            <Space h="md" />
            <Box style={{ width: '100%', minWidth: '280px' }}>
              <Editor 
                contentData={plan.content} 
                editable={date >= dayjs().format("YYYY-MM-DD")} 
              />
            </Box>
          </Card>
        ))}

        {/* 币种详细计划 */}
        {symbolPlans.length > 0 && (
          <Card shadow="sm" padding="lg" radius="md" withBorder>
            <Card.Section withBorder inheritPadding py="xs">
              <Flex justify="space-between" align="center">
                <Title order={4} size="h5" color="grape">币种详细计划</Title>
                <Link href="/assistant/hot-coins" target="_blank">
                  <Text component="a" size="sm" color="blue" td="underline">
                    查看热门币种
                  </Text>
                </Link>
              </Flex>
            </Card.Section>
            <Space h="md" />
            <Stack spacing="sm">
              {symbolPlans.map(plan => (
                <Card key={plan.id} withBorder radius="sm" p={0}>
                  <Group
                    position="apart"
                    p="md"
                    style={{ 
                      cursor: "pointer",
                      backgroundColor: expandedSymbolPlans[plan.id] ? 'var(--mantine-color-gray-0)' : 'transparent'
                    }}
                    onClick={() => toggleSymbolExpand(plan.id)}
                  >
                    <Flex align="center" gap="md">
                      <Badge size="lg" variant="filled" color="grape">
                        {plan.symbol}
                      </Badge>
                      <Text size="sm" color="dimmed">
                        {plan.content ? '已制定策略' : '待补充内容'}
                      </Text>
                    </Flex>
                    <ActionIcon variant="subtle" size="sm">
                      {expandedSymbolPlans[plan.id] ? 
                        <IconChevronUp size={16} /> : 
                        <IconChevronDown size={16} />
                      }
                    </ActionIcon>
                  </Group>
                  
                  {expandedSymbolPlans[plan.id] && (
                    <Box p="md" pt={0} style={{ borderTop: '1px solid var(--mantine-color-gray-2)' }}>
                      <Flex justify="flex-end" mb="md" gap={8}>
                        <Tooltip label="分享计划" withArrow>
                          <Box>
                            <ShareContent
                              contentType="plan"
                              contentId={plan.id}
                              title={`${plan.symbol} 交易计划`}
                              iconSize={18}
                              showText={false}
                            />
                          </Box>
                        </Tooltip>
                        <Tooltip label="删除计划" withArrow>
                          <ActionIcon
                            variant="subtle"
                            size="md"
                            color="red"
                            onClick={(e) => handleDeletePlan(plan.id, date, e)}
                          >
                            <IconTrash size={18} />
                          </ActionIcon>
                        </Tooltip>
                      </Flex>
                      <Box style={{ width: '100%', minWidth: '280px' }}>
                        <Editor
                          contentData={plan.content}
                          editable={date === dayjs().format("YYYY-MM-DD")}
                        />
                      </Box>
                    </Box>
                  )}
                </Card>
              ))}
            </Stack>
          </Card>
        )}
      </Stack>
    );
  };

  return (
    <Container size="xl" px="md" style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      {/* 顶部标题和操作栏 */}
      <Box py="md" style={{ borderBottom: "1px solid var(--mantine-color-gray-2)" }}>
        <Flex justify="space-between" align="center" mb="sm">
          <Text size="xl" c="grape" weight={700}>
            交易计划管理
          </Text>
          <Group spacing="sm">
            <Select
              placeholder="选择计划类型"
              value={planType}
              onChange={setPlanType}
              data={planTypeOptions}
              size="sm"
              w={140}
            />
            <Button 
              // leftIcon={<IconPlus size={16} />}
              onClick={handleNewPlan} 
              loading={loadingMore}
              size="sm"
            >
              新建计划
            </Button>
          </Group>
        </Flex>
      </Box>

      {/* 计划列表 */}
      <Box 
        ref={scrollRef}
        flex={1}
        style={{ 
          overflowY: "auto",
          position: "relative"
        }}
        py="md"
      >
        {Object.keys(plans).length === 0 && !loadingMore ? (
          <Paper withBorder p="xl" radius="md" style={{ textAlign: "center" }}>
            <Text color="dimmed" size="lg" mb="md">
              暂无交易计划
            </Text>
            <Text color="dimmed" size="sm">
              点击上方"新建计划"按钮开始制定您的交易策略
            </Text>
          </Paper>
        ) : (
          <Box style={{ position: "relative", paddingLeft: "1rem" }}>
            {/* 时间线背景 */}
            <Box
              style={{
                position: "absolute",
                left: "0.5rem",
                top: 0,
                bottom: 0,
                width: "2px",
                backgroundColor: "var(--mantine-color-gray-3)",
                zIndex: 0
              }}
            />
            
            <Stack spacing="xl">
              {Object.keys(plans).map((date, index) => {
                const isToday = date === dayjs().format("YYYY-MM-DD");
                const isPast = dayjs(date).isBefore(dayjs(), 'day');
                
                return (
                  <Box key={date} style={{ position: "relative", zIndex: 1 }}>
                    {/* 时间线节点 */}
                    <Box
                      style={{
                        position: "absolute",
                        left: "-0.9rem", // 调整为 -0.7rem 让节点居中在时间线上
                        top: "1rem",
                        width: "1rem",
                        height: "1rem",
                        borderRadius: "50%",
                        backgroundColor: isToday ? "var(--mantine-color-blue-6)" : 
                                       isPast ? "var(--mantine-color-gray-5)" : "var(--mantine-color-grape-6)",
                        border: "3px solid white",
                        boxShadow: "0 2px 4px rgba(0,0,0,0.1)",
                        zIndex: 2
                      }}
                    />
                    
                    <Card 
                      shadow="sm" 
                      padding="lg" 
                      radius="md" 
                      withBorder
                      style={{
                        marginLeft: "1rem",
                        border: isToday ? "2px solid var(--mantine-color-blue-4)" : undefined
                      }}
                    >
                      {/* 日期标题 */}
                      <Card.Section withBorder inheritPadding py="sm">
                        <Flex 
                          justify="space-between" 
                          align="center"
                          style={{ cursor: "pointer" }}
                          onClick={() => toggleExpand(date)}
                        >
                          <Flex align="center" gap="sm">
                            <Text weight={600} size="lg" color={isToday ? "blue" : "grape"}>
                              {dayjs(date).format("MM.DD")} {dayjs(date).format("dddd")}
                            </Text>
                            {isToday && (
                              <Badge size="sm" color="blue" variant="light">
                                今日
                              </Badge>
                            )}
                            {isPast && (
                              <Badge size="sm" color="gray" variant="light">
                                已过去
                              </Badge>
                            )}
                            <Badge size="xs" variant="outline">
                              {plans[date]?.length || 0} 个计划
                            </Badge>
                          </Flex>
                          <ActionIcon variant="subtle" size="sm">
                            {expandedPlans[date] ? 
                              <IconChevronUp size={18} /> : 
                              <IconChevronDown size={18} />
                            }
                          </ActionIcon>
                        </Flex>
                      </Card.Section>

                      {/* 计划内容 */}
                      {expandedPlans[date] && (
                        <Box pt="md">
                          {renderPlanCard(date, plans[date])}
                        </Box>
                      )}
                    </Card>
                  </Box>
                );
              })}
            </Stack>
          </Box>
        )}

        {/* 加载状态 */}
        {(loadingMore || !hasMore) && (
          <Paper withBorder p="md" mt="md" radius="md">
            <Group position="center">
              {loadingMore ? (
                <Group spacing="xs">
                  <Loader size="sm" />
                  <Text size="sm" color="dimmed">加载中...</Text>
                </Group>
              ) : (
                <Text size="sm" color="dimmed">已显示全部计划</Text>
              )}
            </Group>
          </Paper>
        )}
      </Box>

      {/* AI分析结果弹窗 */}
      <Modal
        opened={openAnalysisDialog}
        onClose={() => setOpenAnalysisDialog(false)}
        title="AI分析结果"
        size="lg"
      >
        {loadingAnalysis ? (
          <Group position="center" p="xl">
            <Loader />
          </Group>
        ) : (
          <Text style={{ whiteSpace: "pre-wrap" }}>
            {analysisResult.raw || analysisResult}
          </Text>
        )}
      </Modal>
    </Container>
  );
};

export default TradingPlan;
