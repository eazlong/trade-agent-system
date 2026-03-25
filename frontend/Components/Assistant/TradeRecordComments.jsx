import React, { useState, useEffect } from "react";
import { 
  Text, 
  Textarea, 
  Button, 
  Group, 
  Avatar, 
  Paper, 
  Divider, 
  Switch, 
  Menu, 
  ActionIcon, 
  Loader,
  Box,
  MantineProvider
} from "@mantine/core";
import '@mantine/core/styles.css';
import { 
  IconMessageReply, 
  IconDots, 
  IconSend, 
  IconEdit, 
  IconTrash, 
  IconX 
} from "@tabler/icons-react";
import { getTradeRecordComments, createRecordComment, updateRecordComment, deleteRecordComment } from "services/qtbot.service";
import { useSelector } from "react-redux";

const TradeRecordComments = ({ record }) => {
  const [comments, setComments] = useState([]);
  const [newComment, setNewComment] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [replyTo, setReplyTo] = useState(null);
  const [editingComment, setEditingComment] = useState(null);
  const [editText, setEditText] = useState("");
  const [loading, setLoading] = useState(false);

  const { user } = useSelector((state) => state.auth);

  useEffect(() => {
    if (record?.id && record.bot_id < -900) {
      fetchComments();
    }
  }, [record]);

  const fetchComments = async () => {
    setLoading(true);
    try {
      const data = await getTradeRecordComments(record.id);
      setComments(data);
    } catch (error) {
      console.error("Error fetching comments:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmitComment = async () => {
    if (!newComment.trim()) return;
    
    try {
      const commentData = {
        trade_record_id: record.id,
        content: newComment,
        is_public: isPublic,
        parent_comment_id: replyTo ? replyTo.id : null
      };
      
      await createRecordComment(commentData);
      setNewComment("");
      setReplyTo(null);
      fetchComments();
    } catch (error) {
      console.error("Error posting comment:", error);
    }
  };

  const handleUpdateComment = async () => {
    if (!editText.trim() || !editingComment) return;
    
    try {
      await updateRecordComment(editingComment.id, {
        ...editingComment,
        content: editText,
        is_public: editingComment.is_public
      });
      
      setEditingComment(null);
      setEditText("");
      fetchComments();
    } catch (error) {
      console.error("Error updating comment:", error);
    }
  };

  const handleDeleteComment = async (commentId) => {
    try {
      await deleteRecordComment(commentId);
      fetchComments();
    } catch (error) {
      console.error("Error deleting comment:", error);
    }
  };

  const handleEdit = (comment) => {
    setEditingComment(comment);
    setEditText(comment.content);
  };

  const handleCancelEdit = () => {
    setEditingComment(null);
    setEditText("");
  };

  const handleReply = (comment) => {
    setReplyTo(comment);
  };

  // 检查当前用户是否是评论的作者
  const isCommentOwner = (comment) => {
    return comment?.user_id === (user?.id || null);
  };

  if (!record || record.bot_id > -900) {
    return null;
  }

  const CommentMenu = ({ comment }) => (
    <Menu shadow="md" width={200} position="bottom-end">
      <Menu.Target>
        <ActionIcon size="sm" variant="subtle">
          <IconDots size={16} />
        </ActionIcon>
      </Menu.Target>
      <Menu.Dropdown>
        {!comment.parent_comment_id && (
          <Menu.Item 
            icon={<IconMessageReply size={14} />} 
            onClick={() => handleReply(comment)}
          >
            回复
          </Menu.Item>
        )}
        {isCommentOwner(comment) && (
          <>
            <Menu.Item 
              icon={<IconEdit size={14} />} 
              onClick={() => handleEdit(comment)}
            >
              编辑
            </Menu.Item>
            <Menu.Item 
              color="red" 
              icon={<IconTrash size={14} />} 
              onClick={() => handleDeleteComment(comment.id)}
            >
              删除
            </Menu.Item>
          </>
        )}
      </Menu.Dropdown>
    </Menu>
  );

  return (
    <Box bg="var(--mantine-color-violet-0)" p="md" h="100%">
      <Text fw={600} size="lg" mb="md">交易评论</Text>

      {/* Comment input */}
      <Paper shadow="xs" p="md" mb="lg" radius="md">
        <Textarea
          placeholder={replyTo ? `回复 ${replyTo.username || '用户'}...` : "添加评论..."}
          value={newComment}
          onChange={(e) => setNewComment(e.target.value)}
          minRows={2}
          mb="xs"
        />
        
        <Group justify="space-between" align="center">
          <Switch
            label="公开评论"
            checked={isPublic}
            onChange={(e) => setIsPublic(e.target.checked)}
            size="sm"
          />
          
          <Group>
            {replyTo && (
              <Button
                variant="subtle"
                size="xs"
                leftSection={<IconX size={14} />}
                onClick={() => setReplyTo(null)}
              >
                取消回复
              </Button>
            )}
            
            <Button
              size="xs"
              rightSection={<IconSend size={14} />}
              onClick={handleSubmitComment}
              disabled={!newComment.trim()}
            >
              发布
            </Button>
          </Group>
        </Group>
      </Paper>

      {/* Comments list */}
      {loading ? (
        <Group justify="center" p="md">
          <Loader size="sm" />
          <Text size="sm" c="dimmed">加载中...</Text>
        </Group>
      ) : comments.length > 0 ? (
        <div>
          {comments.map((comment) => (
            <Paper key={comment.id} mb="md" p={0} withBorder>
              <Box p="md">
                <Group justify="space-between" mb="xs">
                  <Group>
                    <Avatar color="violet" radius="xl">
                      {comment.username?.[0] || "U"}
                    </Avatar>
                    <div>
                      <Group gap="xs">
                        <Text fw={500} size="sm">
                          {comment.username || "用户"}
                        </Text>
                        {!comment.is_public && (
                          <Text size="xs" c="dimmed">(私密)</Text>
                        )}
                      </Group>
                      <Text size="xs" c="dimmed">
                        {new Date(comment.created_at).toLocaleString()}
                      </Text>
                    </div>
                  </Group>
                  
                  <Group gap="xs">
                    {isCommentOwner(comment) && (
                      <ActionIcon 
                        color="red" 
                        variant="subtle" 
                        size="sm"
                        onClick={() => handleDeleteComment(comment.id)}
                        title="删除评论"
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    )}
                    <CommentMenu comment={comment} />
                  </Group>
                </Group>
                
                {editingComment?.id === comment.id ? (
                  <Box ml={54}>
                    <Textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      minRows={2}
                      mb="xs"
                    />
                    <Group justify="flex-end">
                      <Button 
                        variant="subtle" 
                        size="xs"
                        onClick={handleCancelEdit}
                      >
                        取消
                      </Button>
                      <Button
                        size="xs"
                        onClick={handleUpdateComment}
                        disabled={!editText.trim()}
                      >
                        更新
                      </Button>
                    </Group>
                  </Box>
                ) : (
                  <Text ml={54}>
                    {comment.content}
                  </Text>
                )}
                
                {/* Replies */}
                {comment.replies && comment.replies.length > 0 && (
                  <Box ml={54} mt="md">
                    {comment.replies.map((reply) => (
                      <Paper key={reply.id} bg="var(--mantine-color-gray-0)" p="sm" radius="md" mb="xs">
                        <Group justify="space-between" mb="xs">
                          <Group>
                            <Avatar color="violet" radius="xl" size="sm">
                              {reply.username?.[0] || "U"}
                            </Avatar>
                            <div>
                              <Group gap="xs">
                                <Text fw={500} size="xs">
                                  {reply.username || "用户"}
                                </Text>
                                {!reply.is_public && (
                                  <Text size="xs" c="dimmed">(私密)</Text>
                                )}
                              </Group>
                              <Text size="xs" c="dimmed">
                                {new Date(reply.created_at).toLocaleString()}
                              </Text>
                            </div>
                          </Group>
                          
                          <Group gap="xs">
                            {isCommentOwner(reply) && (
                              <ActionIcon 
                                color="red" 
                                variant="subtle" 
                                size="xs"
                                onClick={() => handleDeleteComment(reply.id)}
                                title="删除评论"
                              >
                                <IconTrash size={14} />
                              </ActionIcon>
                            )}
                            <CommentMenu comment={reply} />
                          </Group>
                        </Group>
                        
                        {editingComment?.id === reply.id ? (
                          <Box ml={36}>
                            <Textarea
                              value={editText}
                              onChange={(e) => setEditText(e.target.value)}
                              minRows={2}
                              mb="xs"
                              size="xs"
                            />
                            <Group justify="flex-end">
                              <Button 
                                variant="subtle" 
                                size="xs"
                                onClick={handleCancelEdit}
                              >
                                取消
                              </Button>
                              <Button
                                size="xs"
                                onClick={handleUpdateComment}
                                disabled={!editText.trim()}
                              >
                                更新
                              </Button>
                            </Group>
                          </Box>
                        ) : (
                          <Text ml={36} size="sm">
                            {reply.content}
                          </Text>
                        )}
                      </Paper>
                    ))}
                  </Box>
                )}
              </Box>
              <Divider />
            </Paper>
          ))}
        </div>
      ) : (
        <Paper p="md" bg="var(--mantine-color-gray-0)" ta="center">
          <Text c="dimmed">暂无评论，成为第一个评论的人吧！</Text>
        </Paper>
      )}
    </Box>
  );
};

// 使用MantineProvider包装组件，确保样式正确应用
// const TradeRecordComments = (props) => (
//   <MantineProvider>
//     <CommentComponent {...props} />
//   </MantineProvider>
// );

export default TradeRecordComments; 