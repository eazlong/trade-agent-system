// src/components/CrudTable.js
import React, { useState, useCallback } from "react";
// import { useSelector } from "react-redux";
import { useEffect } from "react";
import {
  addUserNotifyConfig,
  updateNotifyConfig,
  deleteNotifyConfig,
} from "services/notify.service";

import {
  getAllSymbols
} from "services/trade.service";

import {
  Table,
  Paper,
  Button,
  TextInput,
  Select,
  MultiSelect,
  Loader,
  Card,
  Text,
  Grid,
  Box,
  Divider,
  Modal,
  ActionIcon,
  Stack,
  Group,
  Title,
  NumberInput,
  Flex,
  Affix,
  ScrollArea
} from "@mantine/core";
import { useMediaQuery } from '@mantine/hooks';
import {
  getUserNotifyConfigs,
  getUserNotifyTemplates,
} from "services/notify.service";
import { notifications } from '@mantine/notifications';
import { IconPlus, IconX, IconEdit, IconTrash } from '@tabler/icons-react';
import { useForm } from '@mantine/form';

const MyNotifyConfigsTable = () => {
  const [data, setData] = useState([]);
  const [temps, setTemps] = useState([]);
  const [openDialog, setOpenDialog] = useState(false);
  const [dialogMode, setDialogMode] = useState('add'); // 'add' or 'edit'
  const form = useForm({
    initialValues: {
      template: { id: "", name: "", content: "" }, // Initialize with default structure
      params: ["ALL"],
      repeat: 0,
      expire: new Date(),
      websocket_url: "",
      websocket_keyword: "",
    },
  });

  // 使用 Mantine 的媒体查询来检测移动设备
  const isMobile = useMediaQuery('(max-width: 768px)');

  const [symbolOptions, setSymbolOptions] = useState([
    "ALL"
  ]); // Add this line to manage options for the dropdown

  const showToast = (message, appearance) => {
    notifications.show({
      title: appearance === 'success' ? '成功' : '错误',
      message: message,
      color: appearance === 'success' ? 'green' : 'red',
    });
  };

  // const { notifyConfigs, templates } = useSelector(
  //   (state) => state.notifyConfigs
  // );

  const [isLoading, setIsLoading] = useState(true);  // 添加 loading 状态

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);  // 开始加载时设置 loading 状态
      try {
        try {
          const symbolOptions = await getAllSymbols();
          // 确保过滤和转换后的符号列表是有效的
          const filteredSymbols = symbolOptions
            .filter(x => typeof x === 'string' && x.endsWith("USDT"))
            .map(x => {
              let p = x.indexOf(":");
              return p === -1 ? x.replace("/", "") : x.substring(0, p).replace("/", "");
            });

          console.log("Available symbols for notify:", filteredSymbols);
          setSymbolOptions(["ALL", ...filteredSymbols]);
        } catch (error) {
          showToast("Failed to fetch symbols: " + error.toString(), 'error');
        }
        try {
          const templates = await getUserNotifyTemplates();
          if (templates != undefined) {
            setTemps(templates || []);
          }
        } catch (error) {
          showToast("Failed to fetch templates: " + error.toString(), "error");
        }

        try {
          const notifyConfigs = await getUserNotifyConfigs();

          if (notifyConfigs != undefined) {
            console.log(notifyConfigs);

            setData(notifyConfigs || []);
          }
        } catch (error) {
          showToast("Failed to fetch notify configs: " + error.toString(), 'error');
        }
      } catch (error) {
        showToast(error.toString(), 'error')
      } finally {
        setIsLoading(false);  // 无论成功失败都结束加载状态
      }
    };

    fetchData();
  }, []);

  const handleOpenDialog = (mode = 'add', id = null) => {
    if (mode === 'edit' && id) {
      const item = data.find((item) => item.id === id);
      if (item) {
        form.setFieldValue("template_id", item.template.id);
        form.setValues({ ...item });
      }
    } else {
      // 重置表单为默认值
      form.setValues({
        template: { id: "", name: "", content: "" },
        params: ["ALL"],
        repeat: 0,
        expire: new Date(),
        websocket_url: "",
        websocket_keyword: "",
      });
    }
    setDialogMode(mode);
    setOpenDialog(true);
  };

  const handleCloseDialog = () => {
    setOpenDialog(false);
  };

  const handleAdd = async () => {
    let u = {
      ...form,
      template_id: form.template.id,
      template: form.template,
      params:
        typeof form.params === "string" ? form.params : form.params?.join(","),
    };
    try {
      await addUserNotifyConfig(u);
      setData([...data, u]);
      setForm({});
      handleCloseDialog();
      showToast("Notification config added successfully", 'success');
    } catch (error) {
      showToast("Failed to add notify config: " + error.toString(), 'error');
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteNotifyConfig(id);
      setData(data.filter((item) => item.id !== id));
      showToast("Notification config deleted successfully", 'success');
    } catch (error) {
      showToast("Failed to delete notify config: " + error.toString(), 'error');
    }
  };

  const handleUpdate = async () => {
    let u = {
      ...form,
      template_id: form.template.id,
      params:
        typeof form.params === "string" ? form.params : form.params?.join(","),
    };
    console.log(u);

    try {
      await updateNotifyConfig(form.id, u);
      setData(data.map((item) => (item.id === form.id ? u : item)));
      setForm({});
      handleCloseDialog();
      showToast("Notification config updated successfully", 'success');
    } catch (error) {
      showToast("Failed to update notify config: " + error.toString(), 'error');
    }
  };

  const formatParams = (params) => {
    if (typeof params === "string") {
      return params.split(",");
    }
    return params;
  };

  // 移动设备上的卡片视图
  const MobileCardView = () => (
    <Stack gap="md">
      {data.map((row) => (
        <Card key={row.id} withBorder shadow="sm" p="md">
          <Stack gap="sm">
            <Text fw={600} c="blue" size="lg">
              {row.template?.name}
            </Text>

            <Box>
              <Text size="sm" c="dimmed">Content:</Text>
              <Text size="sm" style={{ wordBreak: 'break-word' }}>
                {row.template?.content?.length > 100
                  ? `${row.template?.content.substring(0, 100)}...`
                  : row.template?.content}
              </Text>
            </Box>

            <Box>
              <Text size="sm" c="dimmed">Params:</Text>
              <Text size="sm" style={{ wordBreak: 'break-word' }}>{row.params}</Text>
            </Box>

            <Group grow>
              <Box>
                <Text size="sm" c="dimmed">Repeat:</Text>
                <Text size="sm">{row.repeat}</Text>
              </Box>
            </Group>

            <Box>
              <Text size="sm" c="dimmed">Websocket URL:</Text>
              <Text size="sm" style={{ wordBreak: 'break-word' }}>{row.websocket_url}</Text>
            </Box>

            <Box>
              <Text size="sm" c="dimmed">Websocket Keyword:</Text>
              <Text size="sm" style={{ wordBreak: 'break-word' }}>{row.websocket_keyword}</Text>
            </Box>

            <Group justify="space-between" mt="md">
              <Button
                variant="outline"
                size="sm"
                leftSection={<IconEdit size={16} />}
                onClick={() => handleOpenDialog('edit', row.id)}
              >
                Edit
              </Button>
              <Button
                variant="outline"
                color="red"
                size="sm"
                leftSection={<IconTrash size={16} />}
                onClick={() => handleDelete(row.id)}
              >
                Delete
              </Button>
            </Group>
          </Stack>
        </Card>
      ))}
    </Stack>
  );



  return (
    <Box p="md">
      {isLoading ? (
        <Flex justify="center" align="center" style={{ minHeight: '400px' }}>
          <Loader size="lg" />
        </Flex>
      ) : (
        <Stack gap="md">
          <Group justify="flex-end" mb="md">
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={() => handleOpenDialog('add')}
            >
              Add Notification Config
            </Button>
          </Group>

          {data && data.length > 0 ? (
            <>
              {isMobile ? (
                <MobileCardView />
              ) : (
                <ScrollArea>
                  <Table.ScrollContainer minWidth={800}>
                    <Table verticalSpacing="sm" striped highlightOnHover>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>Name</Table.Th>
                          <Table.Th>Content</Table.Th>
                          <Table.Th>Params</Table.Th>
                          <Table.Th>Repeat</Table.Th>
                          <Table.Th>Websocket URL</Table.Th>
                          <Table.Th>Websocket Keyword</Table.Th>
                          <Table.Th>Actions</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {data.map((row) => (
                          <Table.Tr key={row.id}>
                            <Table.Td>{row.template?.name}</Table.Td>
                            <Table.Td style={{ maxWidth: '200px', wordBreak: 'break-word' }}>
                              {row.template?.content?.length > 100
                                ? `${row.template?.content.substring(0, 100)}...`
                                : row.template?.content}
                            </Table.Td>
                            <Table.Td style={{ maxWidth: '150px', wordBreak: 'break-word' }}>{row.params}</Table.Td>
                            <Table.Td>{row.repeat}</Table.Td>
                            <Table.Td style={{ maxWidth: '150px', wordBreak: 'break-word' }}>{row.websocket_url}</Table.Td>
                            <Table.Td style={{ maxWidth: '150px', wordBreak: 'break-word' }}>{row.websocket_keyword}</Table.Td>
                            <Table.Td>
                              <Group gap="xs">
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
                  </Table.ScrollContainer>
                </ScrollArea>
              )}
            </>
          ) : (
            <Text ta="center" c="dimmed" mt="xl">
              No notify configs found
            </Text>
          )}

          {/* 悬浮添加按钮 - 仅在移动设备上显示 */}
          {isMobile && (
            <Affix position={{ bottom: 20, right: 20 }}>
              <ActionIcon
                size="xl"
                radius="xl"
                variant="filled"
                onClick={() => handleOpenDialog('add')}
              >
                <IconPlus size={24} />
              </ActionIcon>
            </Affix>
          )}


        </Stack>
      )}
      {/* 弹出框表单 */}
      <Modal
        opened={openDialog}
        onClose={handleCloseDialog}
        title={dialogMode === 'add' ? 'Add New Notification Config' : 'Edit Notification Config'}
        size={isMobile ? "100%" : "md"}
        fullScreen={isMobile}
        closeOnEscape={false}
        trapFocus={false}
        centered
      >
        <form onSubmit={form.onSubmit(handleAdd)} gap="md">
          <Select
            label="Template"
            placeholder="Select a template"
            value={form.values.template?.id?.toString() || ""}
            onChange={(value) => {
              let f = temps.find((x) => x.id == value);
              if (f) {
                form.setFieldValue("template", f);
              }
            }}
            data={temps.map((template) => ({
              value: template.id.toString(),
              label: template.name
            }))}
          />

          <MultiSelect
            label="Symbol"
            placeholder="Select symbols"
            value={formatParams(form.values.params) || []}
            onChange={(values) => form.setFieldValue("params", values)}
            data={symbolOptions}
            searchable
            clearable
            hidePickedOptions
            maxDropdownHeight={200}
          />

          <NumberInput
            label="Repeat"
            placeholder="Enter repeat count"
            value={form.values.repeat || 0}
            onChange={(value) => form.setFieldValue("repeat", value || 0)}
            min={0}
            allowNegative={false}
            allowDecimal={false}
          />

          <TextInput
            label="Websocket URL"
            placeholder="Enter websocket URL"
            value={form.values.websocket_url || ""}
            onChange={(e) => form.setFieldValue("websocket_url", e.target.value)}
          />

          <TextInput
            label="Websocket Keywords"
            placeholder="Enter websocket keywords"
            value={form.values.websocket_keyword || ""}
            onChange={(e) => form.setFieldValue("websocket_keyword", e.target.value)}
          />

          <Group justify="flex-end" mt="md">
            <Button variant="outline" onClick={handleCloseDialog}>
              Cancel
            </Button>
            <Button
              type="submit"
            >
              {dialogMode === 'add' ? 'Add' : 'Update'}
            </Button>
          </Group>
        </form>
      </Modal>
    </Box>
  );
};

export default MyNotifyConfigsTable;
