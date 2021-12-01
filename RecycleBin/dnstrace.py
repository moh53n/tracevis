#!/usr/bin/env python3
from __future__ import absolute_import, unicode_literals

import argparse
import contextlib
import ipaddress
import json
import os
import time
from datetime import datetime
from socket import socket
from time import sleep

import networkx as nx
from pyvis.network import Network
from scapy.layers.dns import DNS, DNSQR
from scapy.layers.inet import IP, UDP
from scapy.sendrecv import sr1
from scapy.volatile import RandShort

from util.traceroute_struct import Traceroute

LOCALHOST = '127.0.0.1'

SLEEP_TIME = 1
TIMEOUT = 1
ROUTER_COLOR = "green"
WINDOWS_COLOR = "blue"
LINUX_COLOR = "purple"
MIDDLEBOX_COLOR = "red"
NO_RESPONSE_COLOR = "gray"
DEVICE_NAME = {
    ROUTER_COLOR: "Router", WINDOWS_COLOR: "Windows",
    LINUX_COLOR: "Linux", MIDDLEBOX_COLOR: "Middlebox", NO_RESPONSE_COLOR: "unknown"}
ACCESSIBLE_REQUEST_COLORS = ["DarkTurquoise", "LimeGreen", "DodgerBlue",
                             "MediumSlateBlue", "Green", "YellowGreen"]
BLOCKED_REQUEST_COLORS = ["HotPink", "Red",
                          "Orange", "DarkGoldenrod", "Brown", "Magenta"]

DEFAULT_IPS = ["8.8.4.4", "1.0.0.1", "9.9.9.9"]
ACCESSIBLE_ADDRESS = "www.example.com"
DEFAULT_BLOCKED_ADDRESS = "www.twitter.com"

multi_directed_graph = nx.MultiDiGraph()
multi_directed_graph.add_node(
    1, label=LOCALHOST, color="Chocolate", title="start")

measurement_data = [[], []]

TEMPLATE_PATH = os.path.dirname(__file__) + "/templates/template_offline.tmplt"
OUTPUT_DIR = "./output/"


def parse_packet(req_answer, current_ttl, elapsed_ms, packet_size):
    device_color = ""
    if req_answer is not None:
        backttl = 0
        if req_answer[IP].ttl <= 20:
            backttl = int((current_ttl - req_answer[IP].ttl) / 2) + 1
            device_color = MIDDLEBOX_COLOR
        elif req_answer[IP].ttl <= 64:
            backttl = 64 - req_answer[IP].ttl + 1
            device_color = LINUX_COLOR
        elif req_answer[IP].ttl <= 128:
            backttl = 128 - req_answer[IP].ttl + 1
            device_color = WINDOWS_COLOR
        else:
            backttl = 255 - req_answer[IP].ttl + 1
            device_color = ROUTER_COLOR
        print("   <<< answer:"
              + "   ip.src: " + req_answer[IP].src
              + "   ip.ttl: " + str(req_answer[IP].ttl)
              + "   back-ttl: " + str(backttl))
        print("      " + req_answer.summary())
        return req_answer[IP].src, backttl, device_color, elapsed_ms, packet_size, req_answer[IP].ttl
    else:
        print(" *** no response *** ")
        return "***", "***", NO_RESPONSE_COLOR, elapsed_ms, packet_size, 0

# ephemeral_port_reserve() function is based on https://github.com/Yelp/ephemeral-port-reserve


def ephemeral_port_reserve():
    with contextlib.closing(socket()) as s:
        s.bind((LOCALHOST, 0))
        # the connect below deadlocks on kernel >= 4.4.0 unless this arg is greater than zero
        s.listen(1)
        sockname = s.getsockname()
        # these three are necessary just to get the port into a TIME_WAIT state
        with contextlib.closing(socket()) as s2:
            s2.connect(sockname)
            sock, _ = s.accept()
            with contextlib.closing(sock):
                return sockname[1]


def send_packet(request_ip, current_ttl, request_address):
    dns_request = IP(
        dst=request_ip, id=RandShort(), ttl=current_ttl)/UDP(
        sport=ephemeral_port_reserve(), dport=53)/DNS(
            rd=1, id=RandShort(), qd=DNSQR(qname=request_address))
    print(">>>request:"
          + "   ip.dst: " + dns_request[IP].dst
          + "   ip.ttl: " + str(current_ttl))
    start_time = time.perf_counter()
    req_answer = sr1(dns_request, verbose=0, timeout=TIMEOUT)
    end_time = time.perf_counter()
    elapsed_ms = float(format(abs((end_time - start_time) * 1000), '.3f'))
    if req_answer is None:
        packet_size = 0
    else:
        packet_size = len(req_answer)
    return parse_packet(req_answer, current_ttl, elapsed_ms, packet_size)


def visualize(previous_node_id, current_node_id,
              current_node_label, current_node_title, device_color,
              current_edge_title, requset_color):
    if not multi_directed_graph.has_node(current_node_id):
        multi_directed_graph.add_node(current_node_id,
                                      label=current_node_label, color=device_color,
                                      title=current_node_title)
    multi_directed_graph.add_edge(previous_node_id, current_node_id,
                                  color=requset_color, title=current_edge_title)


def styled_tooltips(current_request_colors, current_ttl_str, backttl, request_ip,
                    request_hostname, elapsed_ms, packet_size, repeat_all_steps):
    time_size = 0
    if packet_size != 0:
        time_size = format(elapsed_ms/packet_size, '.3f')
    if elapsed_ms > (TIMEOUT * 1000):
        elapsed_ms = 0
    return ("<pre style=\"color:" + current_request_colors + "\">TTL: "
            + current_ttl_str + "<br/>Back-TTL: " + backttl
            + "<br/>Request to: " + request_ip
            + "<br/>Request for: " + request_hostname
            + "<br/>Time: " + str(elapsed_ms) + "ms"
            + "<br/>Size: " + str(packet_size) + "B"
            + "<br/>Time/Size: " + str(time_size) + "ms/B"
            + "<br/>Repeat step: " + str(repeat_all_steps) + "</pre>")


def already_reached_destination(
        previous_node_id, current_node_ip, access_block_steps, ip_steps):
    if previous_node_id in {
        str(int(ipaddress.IPv4Address(current_node_ip))),
        ("middlebox" + str(int(ipaddress.IPv4Address(current_node_ip))) + "x"
         + str(access_block_steps) + str(ip_steps))}:
        return True
    else:
        return False


def are_equal(original_list, result_list):
    counter = 0
    for item in original_list:
        original_item = str(int(ipaddress.IPv4Address(item)))
        original_item_middlebox = "middlebox" + original_item + "x"
        reault_item_1 = str(result_list[0][counter])
        reault_item_2 = str(result_list[1][counter])
        if reault_item_1 != original_item and not reault_item_1.startswith(
                original_item_middlebox):
            return False
        if reault_item_2 != original_item and not reault_item_2.startswith(
                original_item_middlebox):
            return False
        counter += 1
    return True


def initialize_first_nodes(request_ips):
    nodes = []
    for _ in request_ips:
        nodes.append(1)
    return nodes


def initialize_json_first_nodes(request_ips, annotation, protocol):
    start_time = int(datetime.utcnow().timestamp())
    for request_ip in request_ips:
        measurement_data[0].append(
            Traceroute(
                dst_addr=request_ip, annotation=ACCESSIBLE_ADDRESS,
                src_addr=LOCALHOST, proto=protocol, timestamp=start_time
            )
        )
        measurement_data[1].append(
            Traceroute(
                dst_addr=request_ip, annotation=annotation,
                src_addr=LOCALHOST, proto=protocol, timestamp=start_time
            )
        )


def save_measurement_graph(graph_name, attach_jscss):
    net_vis = Network("1500px", "1500px",
                      directed=True, bgcolor="#eeeeee")
    net_vis.from_nx(multi_directed_graph)
    net_vis.set_edge_smooth('dynamic')
    if attach_jscss:
        net_vis.set_template(TEMPLATE_PATH)
    graph_path = OUTPUT_DIR + graph_name + ".html"
    net_vis.save_graph(graph_path)
    print("saved: " + graph_path)


def save_measurement_data(request_ips, graph_name):
    end_time = int(datetime.utcnow().timestamp())
    measurement_data_json = []
    ip_steps = 0
    while ip_steps < len(request_ips):
        measurement_data[0][ip_steps].set_endtime(end_time)
        measurement_data[1][ip_steps].set_endtime(end_time)
        measurement_data_json.append(measurement_data[0][ip_steps])
        measurement_data_json.append(measurement_data[1][ip_steps])
        ip_steps += 1
    data_path = OUTPUT_DIR + graph_name + ".json"
    with open(data_path, "a") as jsonfile:
        jsonfile.write(json.dumps(measurement_data_json,
                       default=lambda o: o.__dict__, indent=4))
    print("saved: " + data_path)


def get_args():
    parser = argparse.ArgumentParser(description='trace DNS censorship')
    parser.add_argument('-p', '--prefix', action='store',
                        help="prefix for the graph file name")
    parser.add_argument('-i', '--ips', type=str,
                        help="add comma-separated IPs (up to 6)")
    parser.add_argument('-d', '--domain', type=str,
                        help="change the default blocked domain name")
    parser.add_argument('-g', '--graph', action='store_true',
                        help="no further TTL advance after reaching the endpoint")
    parser.add_argument('-a', '--attach', action='store_true',
                        help="attach VisJS javascript and CSS to the HTML file (work offline)")
    args = parser.parse_args()
    return args


def main(args):
    graph_name = ""
    request_ips = DEFAULT_IPS
    blocked_address = DEFAULT_BLOCKED_ADDRESS
    just_graph = False
    attach_jscss = False
    request_ips = []
    if args.get("prefix"):
        graph_name = args["prefix"] + "-dns-graph-" + \
            datetime.utcnow().strftime("%Y%m%d-%H%M")
    else:
        graph_name = "dns-graph-" + datetime.utcnow().strftime("%Y%m%d-%H%M")
    if args.get("ips"):
        request_ips = args["ips"].split(',')
    if args.get("domain"):
        blocked_address = args["domain"]
    if args.get("graph"):
        just_graph = True
    if args.get("attach"):
        attach_jscss = True
    repeat_all_steps = 0
    initialize_json_first_nodes(
        request_ips=request_ips, annotation=blocked_address, protocol="UDP"
    )
    if not os.path.exists(OUTPUT_DIR):
        os.mkdir(OUTPUT_DIR)
    print("− · − · −     − · − · −     − · − · −     − · − · −")
    while repeat_all_steps < 3:
        repeat_all_steps += 1
        previous_node_ids = [
            initialize_first_nodes(request_ips), initialize_first_nodes(request_ips)]
        for current_ttl in range(1, 30):
            if just_graph and are_equal(request_ips, previous_node_ids):
                break
            request_address = ACCESSIBLE_ADDRESS
            current_request_colors = ACCESSIBLE_REQUEST_COLORS
            ip_steps = 0
            access_block_steps = 0
            print(" · · · − − − · · ·     · · · − − − · · ·     · · · − − − · · · ")
            while ip_steps < len(request_ips):
                sleep_time = SLEEP_TIME
                not_yet_destination = not (already_reached_destination(
                    previous_node_ids[access_block_steps][ip_steps],
                    request_ips[ip_steps], access_block_steps, ip_steps))
                if just_graph:
                    if not_yet_destination:
                        answer_ip, backttl, device_color, elapsed_ms, packet_size, req_answer_ttl = send_packet(
                            request_ips[ip_steps], current_ttl, request_address)
                        measurement_data[access_block_steps][ip_steps].add_hop(
                            current_ttl, answer_ip, elapsed_ms, packet_size, req_answer_ttl
                        )
                    else:
                        sleep_time = 0
                else:
                    answer_ip, backttl, device_color, elapsed_ms, packet_size, req_answer_ttl = send_packet(
                        request_ips[ip_steps], current_ttl, request_address)
                    measurement_data[access_block_steps][ip_steps].add_hop(
                        current_ttl, answer_ip, elapsed_ms, packet_size, req_answer_ttl
                    )
                if not_yet_destination:
                    current_node_label = ""
                    current_edge_title = ""
                    current_node_id = "0"
                    current_ttl_str = str(current_ttl)
                    if answer_ip != "***":
                        current_node_id = str(
                            int(ipaddress.IPv4Address(answer_ip)))
                        if device_color == MIDDLEBOX_COLOR:
                            current_node_id = (
                                "middlebox" + str(current_node_id) + "x" + str(access_block_steps) + str(ip_steps))
                        current_node_label = answer_ip
                        current_edge_title = str(backttl)
                    else:
                        current_node_id = (
                            "unknown" + str(previous_node_ids[access_block_steps][ip_steps]) + "x" + current_ttl_str)
                        current_node_label = "***"
                        current_edge_title = "***"
                        sleep_time = 0
                    current_edge_title = styled_tooltips(
                        current_request_colors[ip_steps], current_ttl_str,
                        current_edge_title, request_ips[ip_steps], request_address,
                        elapsed_ms, packet_size, repeat_all_steps)
                    visualize(previous_node_ids[access_block_steps][ip_steps], current_node_id,
                              current_node_label, DEVICE_NAME[device_color], device_color,
                              current_edge_title, current_request_colors[ip_steps])
                    previous_node_ids[access_block_steps][ip_steps] = current_node_id
                print(" · · · − − − · · ·     · · · − − − · · ·     · · · − − − · · · ")
                sleep(sleep_time)
                ip_steps += 1
                if ip_steps == len(request_ips) and access_block_steps == 0:
                    request_address = blocked_address
                    current_request_colors = BLOCKED_REQUEST_COLORS
                    ip_steps = 0
                    access_block_steps = 1
                    print(
                        " ********************************************************************** ")
            save_measurement_graph(graph_name, attach_jscss)  # I can't wait :P
            print(
                " ********************************************************************** ")
            print(
                " ********************************************************************** ")
            print(
                " ********************************************************************** ")
    print("saving measurement data...")
    save_measurement_data(request_ips, graph_name)
    print("saving measurement graph...")
    save_measurement_graph(graph_name, attach_jscss)
    print("finished.")
    print("· · · − · −     · · · − · −     · · · − · −     · · · − · −")


if __name__ == "__main__":
    main(vars(get_args()))