# wrish


reset bluetooth ubuntu
```shell

$ rfkill unblock all

```



nuova tecnica basata su gatttool

```
## restart bluetooth
bluetoothctl power off
bluetoothctl power on
## to prevenet side effect with other device i preferred restart bluetooth
(echo "connect A4:C1:38:9A:A8:2C"; sleep 10; echo "characteristics"; sleep 3) | script -q -c "gatttool -I" /dev/null
```


```
(echo "connect A4:C1:38:9A:A8:2C"; sleep 15; echo "char-read-uuid 00002a00-0000-1000-8000-00805f9b34fb"; sleep 3) | script -q -c "gatttool -I" /dev/null
```
