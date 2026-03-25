// src/components/CrudTable.js
import React, { useState } from "react";
import { useEffect } from "react";
import {
  addBotConfig,
  updateBotConfig,
  deleteBotConfig,
  toggleBotStatus,
} from "services/qtbot.service";

import {
  getAllSymbols
} from "services/trade.service";

import {
  Box,
  Table,
  Paper,
  Button,
  TextInput,
  Select,
  Autocomplete,
  Loader,
  Card,
  Text,
  Title,
  Grid,
  Modal,
  ActionIcon,
  Stack,
  Group
} from "@mantine/core";
import { useMediaQuery } from '@mantine/hooks';
import {
  getUserNotifyTemplates,
} from "services/notify.service";
import {
  getUserBotConfigs
} from "services/qtbot.service";
import { useToasts } from 'react-toast-notifications'
import { IconPlus, IconX, IconPlayerPause, IconEdit, IconTrash } from '@tabler/icons-react';

const UserBotConfigsTable = () => {
  const [data, setData] = useState([]);
  const [temps, setTemps] = useState([]);
  const [openDialog, setOpenDialog] = useState(false);
  const [dialogMode, setDialogMode] = useState('add'); // 'add' or 'edit'
  const [form, setForm] = useState({
    template: { id: "", name: "", content: "" }, // Initialize with default structure
    symbol: ["ALL"],
    bot_name: "",
    params: "",
    repeat: 0,
    expire: new Date(),
    usdt: 0,
  });

  const isMobile = useMediaQuery('(max-width: 768px)');

  const [symbolOptions, setSymbolOptions] = useState([""]); // Add this line to manage options for the dropdown
  const { addToast } = useToasts()
  const showToast = (message, appearance) => {
    addToast(message, { appearance });
  };
  const [isLoading, setIsLoading] = useState(true);  // 添加 loading 状态

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);  // 开始加载时设置 loading 状态
      try {
        const symbolOptions = await getAllSymbols();
        const botConfigs = await getUserBotConfigs();
        const templates = await getUserNotifyTemplates();

        // 确保过滤和转换后的符号列表是有效的
        const filteredSymbols = symbolOptions
          .filter(x => typeof x === 'string' && x.endsWith("USDT"))
          .map(x => {
            let p = x.indexOf(":");
            return p === -1 ? x.replace("/", "") : x.substring(0, p).replace("/", "");
          });

        console.log("Available symbols:", filteredSymbols);
        setSymbolOptions(["ALL", ...filteredSymbols]);

        if (botConfigs != undefined) {
          console.log(botConfigs);
          setData(botConfigs.filter(x => x.deleted == 0) || []);
        }
        if (templates != undefined) {
          setTemps(templates || []);
        }
      } catch (error) {
        showToast(error.toString(), 'error')
      } finally {
        setIsLoading(false);  // 完成加载
      }
    };

    fetchData();
  }, []);

  const handleOpenDialog = (mode = 'add', id = null) => {
    if (mode === 'edit' && id) {
      const item = data.find((item) => item.id === id);
      if (item) {
        setForm({ ...item, template_id: item.template.id });
      }
    } else {
      // 重置表单为默认值
      setForm({
        template: { id: "", name: "", content: "" },
        symbol: ["ALL"],
        bot_name: "",
        params: "",
        repeat: 0,
        expire: new Date(),
        usdt: 0,
      });
    }
    setDialogMode(mode);
    setOpenDialog(true);
  };

  const handleCloseDialog = () => {
    setOpenDialog(false);
  };

  const handleAdd = async () => {
    console.log(form);
    let u = {
      ...form,
      template_id: form.template.id,
      template: form.template,
      symbol:
        typeof form.symbol === "string" ? form.symbol : form.symbol?.join(","),
    };
    console.log(u)
    try {
      await addBotConfig(u);
      setData([...data, u]);
      setForm({});
      handleCloseDialog();
      showToast("Bot config added successfully", 'success');
    } catch (error) {
      showToast("Failed to add bot config: " + error.toString(), 'error');
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteBotConfig(id);
      setData(data.filter((item) => item.id !== id));
      showToast("Bot config deleted successfully", 'success');
    } catch (error) {
      showToast("Failed to delete bot config: " + error.toString(), 'error');
    }
  };

  const handleTogglePause = async (id, currentPauseStatus) => {
    try {
      const newPauseStatus = !currentPauseStatus;
      await toggleBotStatus(id, newPauseStatus);
      setData(data.map((item) => (item.id === id ? { ...item, paused: newPauseStatus } : item)));
      showToast(`Bot ${newPauseStatus ? 'paused' : 'resumed'} successfully`, 'success');
    } catch (error) {
      showToast("Failed to toggle bot status: " + error.toString(), 'error');
    }
  };

  const handleUpdate = async () => {
    let u = {
      ...form,
      template_id: form.template.id,
      symbol:
        typeof form.symbol === "string" ? form.symbol : form.symbol?.join(","),
    };
    console.log(u);

    try {
      await updateBotConfig(form.id, u);
      setData(data.map((item) => (item.id === form.id ? u : item)));
      setForm({
        template: { id: "", name: "", content: "" }, // Initialize with default structure
        symbol: [""],
        bot_name: "",
        params: "",
        repeat: 0,
        expire: new Date(),
        usdt: 0,
        created_at: new Date(),
        updated_at: new Date(),
      });
      handleCloseDialog();
      showToast("Bot config updated successfully", 'success');
    } catch (error) {
      showToast("Failed to update bot config: " + error.toString(), 'error');
    }
  };

  const formatParams = (symbols) => {
    if (typeof symbols === "string") {
      return symbols.split(",");
    }
    return symbols;
  };

  const MobileCardView = () => (
    <Stack>
      {data.map((row) => (
        <Card key={row.id} shadow="md">
          <Grid gutter="xs">
            <Grid.Col span={12}>
              <Text size="lg" weight={700} c="blue">
                {row.template?.name}
              </Text>
            </Grid.Col>

            <Grid.Col span={6}>
              <Text size="xs" c="dimmed">Pause:</Text>
              <Text size="sm">{row.paused ? "Yes" : "No"}</Text>
            </Grid.Col>

            <Grid.Col span={6}>
              <Text size="xs" c="dimmed">Bot Name:</Text>
              <Text size="sm">{row.bot_name}</Text>
            </Grid.Col>

            <Grid.Col span={6}>
              <Text size="xs" c="dimmed">Symbol:</Text>
              <Text size="sm" style={{ wordBreak: 'break-words' }}>{row.symbol}</Text>
            </Grid.Col>

            <Grid.Col span={6}>
              <Text size="xs" c="dimmed">Amount:</Text>
              <Text size="sm">${row.usdt}</Text>
            </Grid.Col>

            <Grid.Col span={12}>
              <Text size="xs" c="dimmed">Params:</Text>
              <Text size="sm" style={{ wordBreak: 'break-words' }}>{row.params}</Text>
            </Grid.Col>

            <Grid.Col span={12} style={{ paddingTop: '8px' }}>
              <Group position="apart">
              <ActionIcon
                variant="outline"
                size="sm"
                onClick={() => handleTogglePause(row.id, row.paused)}
              >
                <IconPlayerPause size={16} />
              </ActionIcon>
              <ActionIcon
                variant="outline"
                size="sm"
                onClick={() => handleOpenDialog('edit', row.id)}
              >
                <IconEdit size={16} />
              </ActionIcon>
              <ActionIcon
                variant="outline"
                color="red"
                size="sm"
                onClick={() => handleDelete(row.id)}
              >
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
            </Grid.Col>
          </Grid>
        </Card>
      ))}
    </Stack>
  );

  return (
    <Box style={{ padding: '16px' }}>
      {isLoading ? (
        <Box style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '400px' }}>
          <Loader />
        </Box>
      ) : (
        <Box>
          <Box style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '16px' }}>
            <Button
              leftSection={<IconPlus />}
              onClick={() => handleOpenDialog('add')}
            >
              Add Bot Config
            </Button>
          </Box>
          {data && data.length > 0 ? (
            <Box>
              {isMobile ? (
                <MobileCardView />
              ) : (
                <Paper style={{ overflowX: 'auto' }}>
                  <Table>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Pause</Table.Th>
                        <Table.Th>Strategy</Table.Th>
                        <Table.Th>Bot Name</Table.Th>
                        <Table.Th>Symbol</Table.Th>
                        <Table.Th>Amount</Table.Th>
                        <Table.Th>Params</Table.Th>
                        <Table.Th>Actions</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {data.map((row) => (
                        <Table.Tr key={row.id}>
                          <Table.Td>{row.paused ? "Yes" : "No"}</Table.Td>
                          <Table.Td>{row.template?.name}</Table.Td>
                          <Table.Td>{row.bot_name}</Table.Td>
                          <Table.Td>{row.symbol}</Table.Td>
                          <Table.Td>${row.usdt}</Table.Td>
                          <Table.Td style={{ maxWidth: '200px', wordBreak: 'break-words' }}>{row.params}</Table.Td>
                          <Table.Td>
                            <Group spacing="xs">
                            <ActionIcon
                                variant="outline"
                                size="sm"
                                onClick={() => handleTogglePause(row.id, row.paused)}
                              >
                                <IconPlayerPause size={16} />
                              </ActionIcon>
                              <ActionIcon
                                variant="outline"
                                size="sm"
                                onClick={() => handleOpenDialog('edit', row.id)}
                              >
                                <IconEdit size={16} />
                              </ActionIcon>
                              <ActionIcon
                                variant="outline"
                                color="red"
                                size="sm"
                                onClick={() => handleDelete(row.id)}
                              >
                                <IconTrash size={16} />
                              </ActionIcon>
                            </Group>
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </Paper>
              )}
            </Box>
          ) : (
            <Box style={{ textAlign: 'center', color: 'gray', marginTop: '16px' }}>
              <Text c="dimmed">No bot configs found</Text>
            </Box>
          )}

          {isMobile && (
            <ActionIcon
              variant="filled"
              color="blue"
              radius="xl"
              size="xl"
              style={{ position: 'fixed', bottom: '24px', right: '24px' }}
              onClick={() => handleOpenDialog('add')}
            >
              <IconPlus size={24} />
            </ActionIcon>
          )}


        </Box>
      )}
      <Modal
        opened={openDialog}
        onClose={handleCloseDialog}
        size="lg"
        fullScreen={isMobile}
        title={
          <Group position="apart" style={{ width: '100%' }}>
            <Title order={4}>
              {dialogMode === 'add' ? 'Add New Bot Config' : 'Edit Bot Config'}
            </Title>
            <ActionIcon onClick={handleCloseDialog}>
              <IconX />
            </ActionIcon>
          </Group>
        }
      >
        <Stack spacing="md" style={{ padding: '8px 0' }}>
          <Select
            label="Strategy"
            data={temps.map(template => ({ value: template.id.toString(), label: template.name }))}
            value={form.template?.id?.toString() || ""}
            onChange={(value) => {
              let f = temps.find((x) => x.id.toString() === value);
              if (f) {
                setForm({
                  ...form,
                  template: f,
                });
              }
            }}
          />

          <Select
            label="Qt Bot"
            data={["HighWinRate", "HighWinRateShort", "Common"].map(x => ({ value: x, label: x }))}
            value={form.bot_name || ""}
            onChange={(value) => {
              setForm({ ...form, bot_name: value });
            }}
          />

          <Autocomplete
            label="Symbol"
            multiple
            data={symbolOptions}
            value={formatParams(form.symbol) || []}
            onChange={(values) =>
              setForm({ ...form, symbol: values })
            }
          />

          <TextInput
            label="Params"
            value={form.params || ""}
            onChange={(e) => {
              const value = e.target.value;
              setForm({ ...form, params: value });
            }}
          />

          <TextInput
            label="Amount($)"
            type="number"
            value={form.usdt || 0}
            onChange={(e) => {
              const value = e.target.value;
              if (
                (Number.isInteger(Number(value)) && Number(value) > 0) ||
                value === ""
              ) {
                setForm({ ...form, usdt: value });
              }
            }}
          />
        </Stack>

        <Group position="right" spacing="sm" style={{ padding: '16px 0 0 0' }}>
          <Button onClick={handleCloseDialog} variant="outline">
            Cancel
          </Button>
          <Button
            onClick={dialogMode === 'add' ? handleAdd : handleUpdate}
          >
            {dialogMode === 'add' ? 'Add' : 'Update'}
          </Button>
        </Group>
      </Modal>
    </Box>
  );
};

export default UserBotConfigsTable;